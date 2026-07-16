#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the strict ``keep_interactions`` contract on validator removal.

Removing a validator decides the fate of the problem's sample interactions, so the
endpoint accepts only the exact strings ``"true"`` and ``"false"``. A plain
``bool`` form field would let FastAPI coerce ``1``, ``on`` and ``yes`` as well, and
the difference between hiding data and destroying it must not hinge on a spelling.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from jinja2 import ChoiceLoader, FileSystemLoader
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_problems import ArenaProblem, ArenaProblemCustomValidator
from arena.models.arena_users import ArenaUser
from arena.routes.admin_problem_validator import router as arena_admin_problem_validator_router
from arena.services import admin_problem_interaction_service, admin_problem_service
from arena.services.token_service import ArenaTokenAction
from shared.enumerations import ArenaRole, CustomValidatorActiveState, CustomValidatorCandidateState
from shared.services.sample_interactions import parse_interaction_text
from web.models.language import Language

TEST_JWT_SECRET = "test-secret-key-for-validator-remove-tests"


def _build_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena app exposing the validator routes."""
    app = FastAPI()
    app.add_middleware(ArenaAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    repo_root = Path(__file__).resolve().parents[2]
    templates = Jinja2Templates(directory=repo_root / "arena" / "template")
    templates.env.loader = ChoiceLoader(
        [
            FileSystemLoader(repo_root / "arena" / "template"),
            FileSystemLoader(repo_root / "shared" / "template"),
        ]
    )
    templates.env.globals["app_version"] = "test"
    templates.env.globals["brand_name"] = "NOCA Arena"
    setup_flash(templates)
    app.state.arena_templates = templates
    app.state.arena_db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    app.state.jwt_service = JWTService(
        config=load_token_config_from_dict(
            {
                "SECRET_KEY": TEST_JWT_SECRET,
                "JWTSERVICE_ALGORITHM": "HS256",
                "JWTSERVICE_ISSUER": "noca-arena-test",
            }
        ),
        logger=logging.getLogger(__name__),
        action_enum=ArenaTokenAction,
    )
    shared_dir = repo_root / "shared"
    arena_dir = repo_root / "arena"
    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/css", StaticFiles(directory=arena_dir / "static" / "css"), name="arena_static_css")
    app.mount("/static/js", StaticFiles(directory=arena_dir / "static" / "js"), name="arena_static_js")
    app.mount("/static/img", StaticFiles(directory=arena_dir / "static" / "img"), name="arena_static_img")

    @app.get("/admin/problems/{problem_id}/edit", name="arena_admin_problem_edit")
    async def _edit(problem_id: str) -> Response:
        return Response(f"edit {problem_id}")

    @app.get("/status", name="arena_status")
    @app.get("/legal/terms", name="arena_terms_of_service")
    @app.get("/legal/privacy", name="arena_privacy_policy")
    async def _footer_stub() -> Response:
        return Response("ok")

    app.include_router(arena_admin_problem_validator_router)
    return app


async def _judge(session: AsyncSession) -> ArenaUser:
    """Create an Arena judge who can edit problems."""
    user = ArenaUser(
        nome="Validator Judge",
        email_normalizado=f"judge-{uuid.uuid4().hex[:8]}@test.example",
        password_hash="hash",
        role=ArenaRole.ARENA_JUDGE,
        can_edit=True,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        session_version=0,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _token(app: FastAPI, user: ArenaUser) -> str:
    """Mint a login token for the given Arena user."""
    return str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": user.get_token_id()},
        )
    )


async def _interactive_problem_with_interaction(
    session: AsyncSession,
    owner: ArenaUser,
) -> ArenaProblem:
    """Create an interactive problem carrying one sample interaction."""
    language = Language(
        id=f"vr-test-{uuid.uuid4().hex[:8]}",
        name="Validator Remove Test Language",
        icon="test",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["true"],
        run_cmd=["true"],
        source_filename="main.txt",
        artifact_path="/sandbox/main.txt",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(language)
    await session.flush()

    problem = await admin_problem_service.create_problem(
        session,
        caller_id=owner.id,
        title="Guess The Number",
        author=None,
        author_is_owner=True,
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="Statement",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        license=None,
        category_ids=[],
    )
    session.add(
        ArenaProblemCustomValidator(
            problem_id=problem.id,
            active_language_id=language.id,
            active_source="print('validator')\n",
            active_state=CustomValidatorActiveState.VALID,
            active_validated_at=datetime.now(UTC),
        )
    )
    await session.flush()
    await session.refresh(problem, attribute_names=["custom_validator"])

    await admin_problem_interaction_service.create_interaction(
        session,
        problem,
        transcript=parse_interaction_text("> 3\n< 5\n> !8"),
    )
    await session.commit()
    return problem


async def _post_remove(app: FastAPI, user: ArenaUser, problem_id: str, data: dict[str, str] | None):
    """POST the validator-removal endpoint with the given form body."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": _token(app, user)},
    ) as client:
        return await client.post(f"/admin/problems/{problem_id}/validator/remove", data=data or {})


async def _get_source_view(app: FastAPI, user: ArenaUser, problem_id: str):
    """GET the validator source view endpoint as the given user."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": _token(app, user)},
    ) as client:
        return await client.get(f"/admin/problems/{problem_id}/validator/source/view")


@pytest.mark.asyncio
async def test_source_view_renders_active_validator_source(session: AsyncSession) -> None:
    """The source view renders the active validator with line-number highlighting."""
    app = _build_app(session)
    judge = await _judge(session)
    problem = await _interactive_problem_with_interaction(session, judge)

    response = await _get_source_view(app, judge, problem.id)

    assert response.status_code == 200
    assert 'body class="arena-validator-source-viewer"' in response.text
    assert "NOCA Arena" in response.text
    assert "Problem 1: Guess The Number" in response.text
    assert "Custom validator source code" in response.text
    assert "print(&#39;validator&#39;)" in response.text
    assert "language-plaintext" in response.text
    assert "data-highlight-line-numbers" in response.text


@pytest.mark.asyncio
async def test_source_view_falls_back_to_candidate_validator_source(session: AsyncSession) -> None:
    """The source view uses the candidate source when no active source exists."""
    app = _build_app(session)
    judge = await _judge(session)
    problem = await _interactive_problem_with_interaction(session, judge)
    assert problem.custom_validator is not None
    problem.custom_validator.active_language_id = None
    problem.custom_validator.active_source = None
    problem.custom_validator.active_state = None
    problem.custom_validator.active_validated_at = None
    problem.custom_validator.candidate_language_id = "python3"
    problem.custom_validator.candidate_source = "print('candidate')\n"
    problem.custom_validator.candidate_token = f"token-{uuid.uuid4()}"
    problem.custom_validator.candidate_state = CustomValidatorCandidateState.PENDING
    await session.commit()

    response = await _get_source_view(app, judge, problem.id)

    assert response.status_code == 200
    assert "print(&#39;candidate&#39;)" in response.text
    assert "language-python" in response.text


@pytest.mark.asyncio
async def test_source_view_returns_404_without_validator_source(session: AsyncSession) -> None:
    """Problems without a validator source should return 404."""
    app = _build_app(session)
    judge = await _judge(session)
    problem = await _interactive_problem_with_interaction(session, judge)
    assert problem.custom_validator is not None
    await session.delete(problem.custom_validator)
    await session.commit()

    response = await _get_source_view(app, judge, problem.id)

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_removal_without_keep_interactions_is_rejected(session: AsyncSession) -> None:
    """The caller must state what happens to the interactions; there is no default."""
    app = _build_app(session)
    judge = await _judge(session)
    problem = await _interactive_problem_with_interaction(session, judge)

    response = await _post_remove(app, judge, problem.id, None)

    assert response.status_code == 422
    assert await admin_problem_interaction_service.count_interactions(session, problem.id) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["1", "0", "on", "yes", "True", "False", ""])
async def test_removal_rejects_every_spelling_but_true_and_false(session: AsyncSession, value: str) -> None:
    """FastAPI would coerce these into a bool; the strict Literal must not."""
    app = _build_app(session)
    judge = await _judge(session)
    problem = await _interactive_problem_with_interaction(session, judge)

    response = await _post_remove(app, judge, problem.id, {"keep_interactions": value})

    assert response.status_code == 422
    assert await admin_problem_interaction_service.count_interactions(session, problem.id) == 1


@pytest.mark.asyncio
async def test_keeping_interactions_hides_them_and_they_resurface(session: AsyncSession) -> None:
    """'true' hides the interactions; staging a validator again brings them back."""
    app = _build_app(session)
    judge = await _judge(session)
    problem = await _interactive_problem_with_interaction(session, judge)

    problem_id = problem.id

    response = await _post_remove(app, judge, problem_id, {"keep_interactions": "true"})

    assert response.status_code == 303
    # The route committed through its own session; drop this one's cached identities.
    session.expire_all()
    assert await session.get(ArenaProblemCustomValidator, problem_id) is None
    # Kept, but out of sight while the problem has no validator.
    assert await admin_problem_interaction_service.list_interactions(session, problem_id) == []
    assert await admin_problem_interaction_service.count_interactions(session, problem_id) == 1

    await admin_problem_interaction_service.unhide_interactions(session, problem_id)
    await session.commit()
    assert len(await admin_problem_interaction_service.list_interactions(session, problem_id)) == 1


@pytest.mark.asyncio
async def test_dropping_interactions_deletes_them_permanently(session: AsyncSession) -> None:
    """'false' destroys the interactions along with the validator."""
    app = _build_app(session)
    judge = await _judge(session)
    problem = await _interactive_problem_with_interaction(session, judge)

    problem_id = problem.id

    response = await _post_remove(app, judge, problem_id, {"keep_interactions": "false"})

    assert response.status_code == 303
    # The route committed through its own session; drop this one's cached identities.
    session.expire_all()
    assert await session.get(ArenaProblemCustomValidator, problem_id) is None
    assert await admin_problem_interaction_service.count_interactions(session, problem_id) == 0
