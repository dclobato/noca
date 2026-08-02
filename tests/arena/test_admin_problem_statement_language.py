#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the Arena problem statement language.

Covers the whole request-side story of one problem property: the create/edit
confirmation rule, the detection endpoint the form calls before submitting, the
import warnings, and the admin list filter.
"""

import io
import json
import zipfile

import pytest
from _admin_problem_app import build_admin_app, create_user, login_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.models.arena_problems import ArenaProblem
from arena.services import admin_problem_service
from shared.enumerations import ArenaRole, StatementLanguage
from shared.services.imageprocessing_service import ImageProcessingService

_PT_STATEMENT = (
    "# Soma de dois numeros\n\n"
    "Dado dois números inteiros, escreva um programa que calcule a soma deles e\n"
    "imprima o resultado na saída padrão do seu programa.\n"
)


def _create_form(**overrides: str) -> dict[str, str]:
    """Build a minimal valid create-problem form payload."""
    data = {
        "title": "Soma",
        "author_is_owner": "true",
        "time_limit_ms": "1000",
        "memory_limit_kb": "262144",
        "pids_limit": "64",
        "output_limit_in_bytes": "65536",
        "problem_statement": _PT_STATEMENT,
    }
    data.update(overrides)
    return data


async def _problem_titled(session: AsyncSession, title: str) -> ArenaProblem | None:
    return (await session.execute(select(ArenaProblem).where(ArenaProblem.title == title))).scalar_one_or_none()


@pytest.mark.asyncio
async def test_problem_create_stores_the_detected_language(session: AsyncSession) -> None:
    """Left on automatic, the language is detected from the statement."""
    app = build_admin_app(session)
    judge = await create_user(session, email="jlang1@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    token = login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.post("/admin/problems/new", data=_create_form(statement_language=""))

    assert response.status_code == 303
    problem = await _problem_titled(session, "Soma")
    assert problem is not None
    assert problem.statement_language == StatementLanguage.PT


@pytest.mark.asyncio
async def test_problem_create_refuses_an_unconfirmed_language_mismatch(session: AsyncSession) -> None:
    """A disagreement is re-rendered for confirmation and commits nothing."""
    app = build_admin_app(session)
    judge = await create_user(session, email="jlang2@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    token = login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post("/admin/problems/new", data=_create_form(statement_language="en"))

    assert response.status_code == 400
    assert 'data-language-conflict-chosen="en"' in response.text
    assert 'data-language-conflict-detected="pt"' in response.text
    assert await _problem_titled(session, "Soma") is None


@pytest.mark.asyncio
async def test_problem_create_accepts_an_acknowledged_language_mismatch(session: AsyncSession) -> None:
    app = build_admin_app(session)
    judge = await create_user(session, email="jlang3@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    token = login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.post(
            "/admin/problems/new",
            data=_create_form(statement_language="en", language_confirmed="en:pt"),
        )

    assert response.status_code == 303
    problem = await _problem_titled(session, "Soma")
    assert problem is not None
    assert problem.statement_language == StatementLanguage.EN


@pytest.mark.asyncio
async def test_problem_create_rejects_an_unsupported_language_value(session: AsyncSession) -> None:
    """An unparsable form value is a stated error, never a 500."""
    app = build_admin_app(session)
    judge = await create_user(session, email="jlang4@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    token = login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post("/admin/problems/new", data=_create_form(statement_language="klingon"))

    assert response.status_code == 400
    assert "Statement language must be one of" in response.text
    assert await _problem_titled(session, "Soma") is None


@pytest.mark.asyncio
async def test_problem_update_stores_a_confirmed_language(session: AsyncSession) -> None:
    app = build_admin_app(session)
    judge = await create_user(session, email="jlang5@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=judge.id,
        title="To Relabel",
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement=_PT_STATEMENT,
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[],
    )
    await session.commit()
    token = login_token(app, judge)
    payload = _create_form(title="To Relabel", statement_language="es", language_confirmed="es:pt")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": token},
    ) as client:
        mismatch = await client.post(
            f"/admin/problems/{problem.id}/edit", data=_create_form(title="To Relabel", statement_language="es")
        )
        confirmed = await client.post(f"/admin/problems/{problem.id}/edit", data=payload)

    assert mismatch.status_code == 400
    assert confirmed.status_code == 303
    await session.refresh(problem)
    assert problem.statement_language == StatementLanguage.ES


@pytest.mark.asyncio
async def test_detect_language_endpoint_reports_detection(session: AsyncSession) -> None:
    app = build_admin_app(session)
    judge = await create_user(session, email="jlang6@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    token = login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        detected = await client.post("/admin/problems/detect-language", json={"statement": _PT_STATEMENT})
        undetectable = await client.post("/admin/problems/detect-language", json={"statement": "Oi."})

    assert detected.status_code == 200
    assert detected.json() == {"language": "pt"}
    assert undetectable.json() == {"language": None}


@pytest.mark.asyncio
async def test_detect_language_endpoint_requires_a_problem_editor(session: AsyncSession) -> None:
    app = build_admin_app(session)
    user = await create_user(session, email="jlang7@test.example", role=ArenaRole.ARENA_USER)
    token = login_token(app, user)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        anonymous = await client.post("/admin/problems/detect-language", json={"statement": _PT_STATEMENT})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as guest_client:
        guest = await guest_client.post("/admin/problems/detect-language", json={"statement": _PT_STATEMENT})

    assert anonymous.status_code == 403
    assert guest.status_code == 401


@pytest.mark.asyncio
async def test_admin_problem_list_filters_by_statement_language(session: AsyncSession) -> None:
    app = build_admin_app(session)
    judge = await create_user(session, email="jlang8@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    for title, language in (("Portuguese Item", StatementLanguage.PT), ("English Item", StatementLanguage.EN)):
        created = await admin_problem_service.create_problem(
            session,
            caller_id=judge.id,
            title=title,
            source=None,
            hide_author_show_source=False,
            time_limit_ms=1000,
            memory_limit_kb=262144,
            pids_limit=64,
            output_limit_in_bytes=65536,
            problem_statement="stmt",
            image_b64=None,
            image_mime=None,
            image_caption=None,
            notes=None,
            category_ids=[],
            statement_language=language,
        )
        assert created.statement_language == language
    await session.commit()

    token = login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        filtered = await client.get("/admin/problems", params={"language": "pt"})
        bogus = await client.get("/admin/problems", params={"language": "klingon"})

    assert filtered.status_code == 200
    assert "Portuguese Item" in filtered.text
    assert "English Item" not in filtered.text
    assert bogus.status_code == 200
    assert "Portuguese Item" in bogus.text
    assert "English Item" in bogus.text


def _language_package(language: str | None, statement: str = _PT_STATEMENT) -> bytes:
    """Build a minimal importable package, optionally stating a language."""
    meta: dict[str, object] = {
        "title": "Imported Soma",
        "time_limit_ms": 1000,
        "memory_limit_kb": 262144,
        "pids_limit": 64,
    }
    if language is not None:
        meta["statement_language"] = language
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("problem.json", json.dumps(meta))
        archive.writestr("statement.md", statement)
        archive.writestr("in/001.in", "1\n")
        archive.writestr("out/001.out", "1\n")
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("language", "statement", "expected_flash"),
    [
        (None, _PT_STATEMENT, "it was detected as Portuguese"),
        (None, "# X\n\nOi.\n", "could not be detected"),
        ("en", _PT_STATEMENT, None),
    ],
)
@pytest.mark.asyncio
async def test_problem_import_warns_about_an_unstated_language(
    session: AsyncSession,
    language: str | None,
    statement: str,
    expected_flash: str | None,
) -> None:
    """A package that stated no language asks the importer to verify the result."""
    app = build_admin_app(session)
    app.state.image_service = ImageProcessingService()
    judge = await create_user(
        session, email=f"jimp{language or 'none'}@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True
    )
    token = login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=True,
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.post(
            "/admin/problems/import",
            files={"package": ("problem.zip", _language_package(language, statement), "application/zip")},
        )

    assert response.status_code == 200
    if expected_flash is None:
        assert "did not state a statement language" not in response.text
    else:
        assert expected_flash in response.text


@pytest.mark.asyncio
async def test_problem_edit_form_preselects_the_stored_language(session: AsyncSession) -> None:
    """The stored language must be selected, or an unrelated save would re-detect over it."""
    app = build_admin_app(session)
    judge = await create_user(session, email="jlang9@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=judge.id,
        title="Preselected",
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement=_PT_STATEMENT,
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[],
        statement_language=StatementLanguage.ES,
    )
    await session.commit()
    token = login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get(f"/admin/problems/{problem.id}/edit")

    assert response.status_code == 200
    select_markup = " ".join(response.text.split('id="statement_language"', 1)[1].split("</select>", 1)[0].split())
    assert '<option value="es" selected>' in select_markup
    assert "selected" not in select_markup.split('value="es"')[0]


@pytest.mark.asyncio
async def test_import_page_documents_the_statement_language_field(session: AsyncSession) -> None:
    """The import page's own field list is what importers read; it must not drift."""
    app = build_admin_app(session)
    judge = await create_user(session, email="jimport-doc@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    token = login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/problems/import")

    assert response.status_code == 200
    assert "<code>statement_language</code>" in response.text
    assert "detected from the statement" in response.text
