#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route behavior for the Contest problem illustration image.

Covers the create and edit POST handlers: an upload persists, a replacement wins
over the remove checkbox, the checkbox clears bytes and MIME, a caption-only edit
leaves the image alone, the limits-only tab never touches either, and an
oversized upload is rejected without persisting anything.
"""

from __future__ import annotations

import io
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from jinja2 import ChoiceLoader, FileSystemLoader
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from shared.enumerations import CustomValidatorActiveState, ProblemValidatorType
from shared.services.imageprocessing_service import ImageProcessingService
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.models.contest import Contest
from web.models.problem import Problem, ProblemCustomValidator
from web.models.users import UberAdmin
from web.routes.contest_admin_problem import router as problem_router
from web.routes.contest_admin_problem_edit import router as problem_edit_router
from web.routes.contest_admin_problem_io import router as problem_io_router
from web.routes.contest_admin_problem_judgment_pages import router as problem_judgment_pages_router
from web.routes.contest_admin_problem_judgment_tc import router as problem_judgment_tc_router
from web.routes.contest_admin_problem_limits import router as problem_limits_router
from web.routes.contest_admin_problem_tc import router as problem_tc_router
from web.routes.contest_admin_problem_tc_pages import router as problem_tc_pages_router
from web.routes.contest_admin_problem_validator import router as problem_validator_router
from web.routes.session import router as session_router
from web.template_globals import register_template_globals

REPO_ROOT = Path(__file__).resolve().parents[2]


def _png_bytes(*, width: int = 8, height: int = 8, color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _gif_bytes() -> bytes:
    """Return a valid GIF image."""
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 0, 0)).save(buffer, format="GIF")
    return buffer.getvalue()


def _oversized_png_bytes() -> bytes:
    """A valid PNG larger than the 2 MB problem-image cap.

    Random noise is used because a flat color compresses far below the cap.
    """

    noise = os.urandom(1600 * 1600 * 3)
    image = Image.frombytes("RGB", (1600, 1600), noise)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=0)
    payload = buffer.getvalue()
    assert len(payload) > 2 * 1024 * 1024
    return payload


@pytest_asyncio.fixture
async def upcoming_contest(session: AsyncSession, uberadmin: UberAdmin) -> Contest:
    """A contest that has not started yet, so problem editing is allowed."""
    contest = Contest(
        contest_name="Upcoming Contest",
        contest_url="http://upcoming.example.com",
        login_slug="upcoming-contest",
        start_time=datetime.now(UTC) + timedelta(hours=2),
        duration_minutes=120,
        stop_answers_after=120,
        stop_updating_scoreboard=120,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    session.add(contest)
    await session.flush()
    return contest


def _build_app(session: AsyncSession, contest: Contest, actor: UberAdmin, tmp_path: Path) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    templates = Jinja2Templates(directory=REPO_ROOT / "web" / "template")
    # Mirror web/main.py so shared/_partials (the image field) resolve.
    templates.env.loader = ChoiceLoader(
        [
            FileSystemLoader(REPO_ROOT / "web" / "template"),
            FileSystemLoader(REPO_ROOT / "shared" / "template"),
        ]
    )
    templates.env.globals["app_version"] = "test"
    templates.env.globals["brand_name"] = "NOCA Contest"
    register_template_globals(templates)
    # `_base.html` resolves the keepalive route on every authenticated page.
    app.include_router(session_router)
    templates.env.globals["contest_minutes"] = lambda seconds: None if seconds is None else seconds // 60
    setup_flash(templates)
    app.state.templates = templates
    app.state.db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    app.state.image_service = ImageProcessingService(logger=logging.getLogger(__name__))
    app.state.valkey_runtime = object()

    shared_dir = REPO_ROOT / "shared"
    web_dir = REPO_ROOT / "web"
    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/shared/js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")
    app.mount("/static/css", StaticFiles(directory=web_dir / "static" / "css"), name="static_css")
    app.mount("/static/js", StaticFiles(directory=web_dir / "static" / "js"), name="static_js")
    app.mount("/static/img", StaticFiles(directory=web_dir / "static" / "img"), name="static_img")

    @app.get("/login", name="login_get")
    @app.get("/logout", name="logout")
    @app.get("/profile", name="profile_get")
    @app.get("/uberadmin", name="uberadmin_dashboard")
    async def _stub() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/c/{slug}", name="contest_dashboard")
    @app.get("/c/{slug}/clock", name="contest_clock")
    @app.get("/c/{slug}/admin", name="view")
    async def _contest_stub(slug: str) -> dict[str, str]:
        return {"slug": slug}

    @app.get("/c/{slug}/solution-tests/", name="contest_solution_tests")
    async def _solution_tests(slug: str) -> dict[str, str]:
        return {"slug": slug}

    @app.get("/assets/balloon/{color}", name="balloon")
    async def _balloon(color: str) -> dict[str, str]:
        return {"color": color}

    @app.get("/admin/categories/autocomplete", name="problem_categories_autocomplete")
    async def _categories_autocomplete() -> list[str]:
        return []

    app.include_router(problem_router)
    app.include_router(problem_edit_router)
    app.include_router(problem_judgment_tc_router)
    app.include_router(problem_judgment_pages_router)
    app.include_router(problem_validator_router)
    app.include_router(problem_tc_router)
    app.include_router(problem_tc_pages_router)
    app.include_router(problem_limits_router)
    app.include_router(problem_io_router)

    async def _override_ctx() -> ContestAdminContext:
        return ContestAdminContext(contest=contest, session=session, actor=actor)

    app.dependency_overrides[get_contest_admin_context] = _override_ctx
    return app


@pytest_asyncio.fixture
async def client(
    session: AsyncSession,
    upcoming_contest: Contest,
    uberadmin: UberAdmin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncClient:
    # conftest already points PROBLEM_STATEMENT_DIR / PROBLEM_TESTCASE_DIR at
    # temp directories for the whole suite.
    app = _build_app(session, upcoming_contest, uberadmin, tmp_path)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False)


def _create_form() -> dict[str, str]:
    return {
        "title": "Problem With Image",
        "color": "#ff0000",
        "author": "Author",
        "notes": "",
        "time_limit_ms": "1000",
        "memory_limit_kb": "262144",
        "pids_limit": "64",
        "output_limit_in_bytes": "",
        "category_names": "",
        "statement_source": "md",
        "md_content": "# Statement",
        "tc_in_0": "1 2",
        "tc_out_0": "3",
    }


def _edit_form(problem: Problem) -> dict[str, str]:
    return {
        "title": problem.title,
        "color": problem.color,
        "author": problem.author or "",
        "notes": problem.notes or "",
        "time_limit_ms": str(problem.time_limit_ms),
        "memory_limit_kb": str(problem.memory_limit_kb),
        "pids_limit": str(problem.pids_limit),
        "output_limit_in_bytes": "",
        "active_tab": "content",
        "category_names": "",
        "statement_source": "unchanged",
        "md_content": "",
    }


async def _create_problem_with_image(
    client: AsyncClient,
    session: AsyncSession,
    slug: str,
    *,
    caption: str = "A red square",
) -> Problem:
    response = await client.post(
        f"/c/{slug}/admin/problems/new/standard",
        data={**_create_form(), "image_caption": caption},
        files={"image": ("figure.png", _png_bytes(), "image/png")},
    )
    assert response.status_code == 303, response.text
    result = await session.scalars(select(Problem).where(Problem.title == "Problem With Image"))
    return result.one()


async def _create_problem_with_validator(session: AsyncSession, contest: Contest) -> Problem:
    """Create a contest problem carrying an active Python validator."""
    problem = Problem(
        contest_id=contest.id,
        title="Validator Problem",
        ordinal=7,
        color="#2f9e41",
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(problem)
    await session.flush()
    session.add(
        ProblemCustomValidator(
            problem_id=problem.id,
            active_language_id="python3",
            active_source="print('validator')\n",
            active_state=CustomValidatorActiveState.VALID,
            active_validated_at=datetime.now(UTC),
        )
    )
    await session.commit()
    await session.refresh(problem, attribute_names=["custom_validator"])
    return problem


@pytest.mark.asyncio
async def test_validator_source_view_renders_active_source(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,
) -> None:
    """The contest source view renders the active validator with line numbers."""
    problem = await _create_problem_with_validator(session, upcoming_contest)

    response = await client.get(f"/c/{upcoming_contest.login_slug}/admin/problems/{problem.id}/validator/source/view")

    assert response.status_code == 200
    assert "NOCA Contest" in response.text
    assert "Upcoming Contest" in response.text
    assert "Problem G: Validator Problem" in response.text
    assert "Custom validator source code" in response.text
    assert "print(&#39;validator&#39;)" in response.text
    assert "language-python" in response.text
    assert "data-highlight-line-numbers" in response.text


@pytest.mark.asyncio
async def test_validator_source_view_returns_404_without_source(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,
) -> None:
    """The contest source view returns 404 when no validator source exists."""
    problem = Problem(
        contest_id=upcoming_contest.id,
        title="Plain Problem",
        ordinal=8,
        color="#2f9e41",
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(problem)
    await session.commit()

    response = await client.get(f"/c/{upcoming_contest.login_slug}/admin/problems/{problem.id}/validator/source/view")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_persists_image_and_caption(
    client: AsyncClient, session: AsyncSession, upcoming_contest: Contest
) -> None:
    problem = await _create_problem_with_image(client, session, upcoming_contest.login_slug)

    assert problem.problem_image_base64
    assert problem.problem_image_mime == "image/png"
    assert problem.problem_image_caption == "A red square"


@pytest.mark.asyncio
async def test_edit_form_advertises_gif_support(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,
) -> None:
    """The Contest problem edit form lists GIF in its shared upload field."""
    problem = await _create_problem_with_image(client, session, upcoming_contest.login_slug)

    response = await client.get(f"/c/{upcoming_contest.login_slug}/admin/problems/{problem.id}/edit")

    assert response.status_code == 200
    assert "GIF, JPEG, PNG, or WebP" in response.text
    assert 'accept=".gif,.png,.jpg,.jpeg,.webp"' in response.text


@pytest.mark.asyncio
async def test_create_persists_gif_image(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,
) -> None:
    """The Contest problem route accepts GIF illustration images."""
    response = await client.post(
        f"/c/{upcoming_contest.login_slug}/admin/problems/new/standard",
        data=_create_form(),
        files={"image": ("figure.gif", _gif_bytes(), "image/gif")},
    )

    assert response.status_code == 303, response.text
    problem = (await session.scalars(select(Problem).where(Problem.title == "Problem With Image"))).one()
    assert problem.problem_image_base64
    assert problem.problem_image_mime == "image/gif"


@pytest.mark.asyncio
async def test_create_without_image_leaves_columns_null(
    client: AsyncClient, session: AsyncSession, upcoming_contest: Contest
) -> None:

    response = await client.post(
        f"/c/{upcoming_contest.login_slug}/admin/problems/new/standard",
        data=_create_form(),
    )
    assert response.status_code == 303, response.text

    problem = (await session.scalars(select(Problem).where(Problem.title == "Problem With Image"))).one()
    assert problem.problem_image_base64 is None
    assert problem.problem_image_mime is None
    assert problem.problem_image_caption is None


@pytest.mark.asyncio
async def test_edit_replacement_image_overwrites_previous(
    client: AsyncClient, session: AsyncSession, upcoming_contest: Contest
) -> None:
    problem = await _create_problem_with_image(client, session, upcoming_contest.login_slug)
    original = problem.problem_image_base64

    response = await client.post(
        f"/c/{upcoming_contest.login_slug}/admin/problems/{problem.id}/edit",
        data={**_edit_form(problem), "image_caption": "A blue square"},
        files={"image": ("other.png", _png_bytes(color=(0, 0, 255)), "image/png")},
    )
    assert response.status_code == 303, response.text

    await session.refresh(problem)
    assert problem.problem_image_base64
    assert problem.problem_image_base64 != original
    assert problem.problem_image_mime == "image/png"
    assert problem.problem_image_caption == "A blue square"


@pytest.mark.asyncio
async def test_edit_clear_image_removes_bytes_mime_and_caption(
    client: AsyncClient, session: AsyncSession, upcoming_contest: Contest
) -> None:
    """Removing the image removes its caption, even when one is still submitted.

    The caption field stays populated in the form when the remove checkbox is
    ticked, so the route must not keep a caption with no image to caption.
    """
    problem = await _create_problem_with_image(client, session, upcoming_contest.login_slug)

    response = await client.post(
        f"/c/{upcoming_contest.login_slug}/admin/problems/{problem.id}/edit",
        data={**_edit_form(problem), "image_caption": "A red square", "clear_image": "true"},
    )
    assert response.status_code == 303, response.text

    await session.refresh(problem)
    assert problem.problem_image_base64 is None
    assert problem.problem_image_mime is None
    assert problem.problem_image_caption is None


@pytest.mark.asyncio
async def test_edit_new_upload_wins_over_clear_image(
    client: AsyncClient, session: AsyncSession, upcoming_contest: Contest
) -> None:
    problem = await _create_problem_with_image(client, session, upcoming_contest.login_slug)
    original = problem.problem_image_base64

    response = await client.post(
        f"/c/{upcoming_contest.login_slug}/admin/problems/{problem.id}/edit",
        data={**_edit_form(problem), "image_caption": "Replaced", "clear_image": "true"},
        files={"image": ("other.png", _png_bytes(color=(0, 255, 0)), "image/png")},
    )
    assert response.status_code == 303, response.text

    await session.refresh(problem)
    assert problem.problem_image_base64 is not None
    assert problem.problem_image_base64 != original
    assert problem.problem_image_caption == "Replaced"


@pytest.mark.asyncio
async def test_edit_caption_only_keeps_image(
    client: AsyncClient, session: AsyncSession, upcoming_contest: Contest
) -> None:
    problem = await _create_problem_with_image(client, session, upcoming_contest.login_slug)
    original = problem.problem_image_base64

    response = await client.post(
        f"/c/{upcoming_contest.login_slug}/admin/problems/{problem.id}/edit",
        data={**_edit_form(problem), "image_caption": "Renamed caption"},
    )
    assert response.status_code == 303, response.text

    await session.refresh(problem)
    assert problem.problem_image_base64 == original
    assert problem.problem_image_mime == "image/png"
    assert problem.problem_image_caption == "Renamed caption"


@pytest.mark.asyncio
async def test_edit_empty_caption_clears_caption_but_keeps_image(
    client: AsyncClient, session: AsyncSession, upcoming_contest: Contest
) -> None:
    problem = await _create_problem_with_image(client, session, upcoming_contest.login_slug)
    original = problem.problem_image_base64

    response = await client.post(
        f"/c/{upcoming_contest.login_slug}/admin/problems/{problem.id}/edit",
        data={**_edit_form(problem), "image_caption": "   "},
    )
    assert response.status_code == 303, response.text

    await session.refresh(problem)
    assert problem.problem_image_base64 == original
    assert problem.problem_image_caption is None


@pytest.mark.asyncio
async def test_limits_only_tab_leaves_image_and_caption_untouched(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
    tmp_path: Path,
) -> None:
    """A running contest allows only the limits tab; it must not clear the image.

    The limits-only branch never reads the image form fields, so a POST without
    them (the tab does not render them) must leave both columns alone.
    """
    problem = Problem(
        title="Running Problem",
        color="#00ff00",
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        problem_image_base64="AAAA",
        problem_image_mime="image/png",
        problem_image_caption="Keep me",
        validator_type=ProblemValidatorType.STANDARD,
    )
    problem.contest_id = running_contest.id
    problem.ordinal = 1
    session.add(problem)
    await session.flush()

    app = _build_app(session, running_contest, uberadmin, tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as running_client:
        response = await running_client.post(
            f"/c/{running_contest.login_slug}/admin/problems/{problem.id}/edit",
            data={"active_tab": "limits"},
        )
    assert response.status_code == 303, response.text

    await session.refresh(problem)
    assert problem.problem_image_base64 == "AAAA"
    assert problem.problem_image_mime == "image/png"
    assert problem.problem_image_caption == "Keep me"


@pytest.mark.asyncio
async def test_oversized_image_is_rejected_and_nothing_persists(
    client: AsyncClient, session: AsyncSession, upcoming_contest: Contest
) -> None:

    response = await client.post(
        f"/c/{upcoming_contest.login_slug}/admin/problems/new/standard",
        data={**_create_form(), "image_caption": "Too big"},
        files={"image": ("huge.png", _oversized_png_bytes(), "image/png")},
    )

    assert response.status_code == 422
    assert "Problem image" in response.text

    count = await session.scalar(select(func.count()).select_from(Problem))
    assert count == 0
