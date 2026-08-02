#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route, SQLite, and form contracts for Arena problem text suggestions."""

from __future__ import annotations

from pathlib import Path

import pytest
from _admin_problem_app import build_admin_app as _build_admin_app
from _admin_problem_app import create_user as _create_user
from _admin_problem_app import login_token as _login_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_problems import ArenaProblem
from arena.services import admin_problem_service
from arena.services.problem_search_service import ProblemSuggestionField
from shared.enumerations import ArenaRole

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "arena" / "static" / "js" / "admin-problem-form.js"


async def _create_problem(
    session: AsyncSession,
    *,
    owner_id: str,
    title: str,
    source: str | None = None,
    author: str | None = None,
) -> ArenaProblem:
    """Create a disabled problem with only fields relevant to suggestion tests."""
    return await admin_problem_service.create_problem(
        session,
        caller_id=owner_id,
        title=title,
        source=source,
        author=author,
        author_is_owner=author is None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="Statement text.",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[],
    )


async def _suggestions(
    session: AsyncSession,
    *,
    field: ProblemSuggestionField,
    query: str,
    caller_id: str,
    is_admin: bool,
) -> list[str]:
    """Call the service with a field value narrowed by the test's static contract."""
    return await admin_problem_service.search_problem_suggestions(
        session,
        field=field,
        query=query,
        caller_id=caller_id,
        is_admin=is_admin,
    )


@pytest.mark.asyncio
async def test_suggestions_endpoint_requires_an_editor_and_validates_queries(
    session: AsyncSession,
) -> None:
    """The endpoint requires a problem editor and exposes only the fixed query contract."""
    app = _build_admin_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        assert (await client.get("/admin/problems/suggestions?field=source&q=ab")).status_code == 401

    member = await _create_user(session, email="suggestions-member@noca.invalid")
    editor = await _create_user(
        session,
        email="suggestions-editor@noca.invalid",
        role=ArenaRole.ARENA_JUDGE,
        can_edit=True,
    )
    member_token = _login_token(app, member)
    editor_token = _login_token(app, editor)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": member_token},
    ) as client:
        assert (await client.get("/admin/problems/suggestions?field=source&q=ab")).status_code == 403

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": editor_token},
    ) as client:
        assert (await client.get("/admin/problems/suggestions?q=ab")).status_code == 422
        assert (await client.get("/admin/problems/suggestions?field=title&q=ab")).status_code == 422
        assert (await client.get("/admin/problems/suggestions?field=source&q=a")).status_code == 422
        assert (
            await client.get("/admin/problems/suggestions", params={"field": "source", "q": "x" * 257})
        ).status_code == 422


@pytest.mark.asyncio
async def test_suggestions_endpoint_caps_string_payload_and_scopes_visibility(
    session: AsyncSession,
) -> None:
    """Editors see enabled values plus their drafts; admins see every stored value."""
    app = _build_admin_app(session)
    editor = await _create_user(
        session,
        email="suggestions-owner@noca.invalid",
        role=ArenaRole.ARENA_JUDGE,
        can_edit=True,
    )
    other_editor = await _create_user(
        session,
        email="suggestions-other@noca.invalid",
        role=ArenaRole.ARENA_JUDGE,
        can_edit=True,
    )
    admin = await _create_user(
        session,
        email="suggestions-admin@noca.invalid",
        role=ArenaRole.ARENA_ADMIN,
    )
    for number in range(17):
        await _create_problem(
            session,
            owner_id=editor.id,
            title=f"Owner archive {number}",
            source=f"Archive {number:02d}",
        )
    await _create_problem(
        session,
        owner_id=other_editor.id,
        title="Other editor archive",
        source="Archive external",
    )
    shared_problem = await _create_problem(
        session,
        owner_id=other_editor.id,
        title="Other editor published archive",
        source="Shared catalog",
    )
    shared_problem.enabled = True
    await session.commit()

    async def get_for(token: str, query: str = "Archive") -> list[str]:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            cookies={"arena_access_token": token},
        ) as client:
            response = await client.get("/admin/problems/suggestions", params={"field": "source", "q": query})
        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {"suggestions"}
        suggestions = payload["suggestions"]
        assert isinstance(suggestions, list)
        assert all(isinstance(value, str) for value in suggestions)
        return suggestions

    editor_suggestions = await get_for(_login_token(app, editor))
    assert editor_suggestions == [f"Archive {number:02d}" for number in range(15)]

    admin_suggestions = await get_for(_login_token(app, admin))
    assert len(admin_suggestions) == 15
    assert await get_for(_login_token(app, editor), "shared") == ["Shared catalog"]
    assert await get_for(_login_token(app, admin), "shared") == ["Shared catalog"]
    assert await get_for(_login_token(app, editor), "external") == []
    assert await get_for(_login_token(app, admin), "external") == ["Archive external"]


@pytest.mark.asyncio
async def test_sqlite_suggestions_filter_trim_deduplicate_and_treat_wildcards_literally(
    session: AsyncSession,
) -> None:
    """Portable matching keeps only stored free text and never interprets LIKE wildcards."""
    owner = await _create_user(
        session,
        name="Owner-backed Name",
        email="suggestions-sqlite-owner@noca.invalid",
        role=ArenaRole.ARENA_ADMIN,
    )
    await _create_problem(session, owner_id=owner.id, title="One", source="Gamma")
    duplicate = await _create_problem(session, owner_id=owner.id, title="Two", source="Gamma")
    case_variant = await _create_problem(session, owner_id=owner.id, title="Three", source="gamma")
    blank = await _create_problem(session, owner_id=owner.id, title="Four", source=" ")
    await _create_problem(session, owner_id=owner.id, title="Five", source=None)
    await _create_problem(session, owner_id=owner.id, title="Owner-backed author")
    await _create_problem(
        session,
        owner_id=owner.id,
        title="Free-text author",
        author="Owner-backed Name Studio",
    )
    literal = await _create_problem(session, owner_id=owner.id, title="Literal", source="50%_off")
    await _create_problem(session, owner_id=owner.id, title="Wildcard decoy", source="50AXoff")
    duplicate.source = "  Gamma  "
    case_variant.source, blank.source, literal.source = "gamma", " ", "  50%_off  "
    await session.flush()

    async def scoped_suggestions(field: ProblemSuggestionField, query: str) -> list[str]:
        """Search as the test's admin owner."""
        return await _suggestions(session, field=field, query=query, caller_id=owner.id, is_admin=True)

    assert await scoped_suggestions("source", "ga") == ["Gamma", "gamma"]
    assert await scoped_suggestions("source", "50%_off") == ["50%_off"]
    assert await scoped_suggestions("author", "Owner") == ["Owner-backed Name Studio"]
    assert await scoped_suggestions("source", "  ") == []
    assert await scoped_suggestions("source", "Unlisted source") == []
    created = await _create_problem(
        session,
        owner_id=owner.id,
        title="New arbitrary values",
        source="Unlisted source",
        author="Unlisted contributor",
    )
    await session.flush()
    assert (created.source, created.author, created.author_is_owner) == (
        "Unlisted source",
        "Unlisted contributor",
        False,
    )


@pytest.mark.asyncio
async def test_form_wires_suggestions_in_create_and_edit_modes(session: AsyncSession) -> None:
    """Both forms expose native datalists while keeping their existing free-text inputs."""
    app = _build_admin_app(session)
    editor = await _create_user(
        session,
        email="suggestions-form@noca.invalid",
        role=ArenaRole.ARENA_JUDGE,
        can_edit=True,
    )
    problem = await _create_problem(session, owner_id=editor.id, title="Editable")
    await session.commit()
    token = _login_token(app, editor)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        create_response = await client.get("/admin/problems/new")
        edit_response = await client.get(f"/admin/problems/{problem.id}/edit")

    for response in (create_response, edit_response):
        assert response.status_code == 200
        assert all(value in response.text for value in ('list="source-suggestions"', 'list="author-suggestions"'))
        assert response.text.count('data-suggestions-field="source"') == 1
        assert response.text.count('data-suggestions-field="author"') == 1
        assert response.text.count('data-suggestions-url="http://testserver/admin/problems/suggestions"') == 2
        assert all(value in response.text for value in ('id="source-suggestions"', 'id="author-suggestions"'))


def test_suggestion_script_debounces_aborts_per_input_and_uses_safe_option_nodes() -> None:
    """The external client code keeps autocomplete advisory and isolated per text input."""
    script = _SCRIPT.read_text(encoding="utf-8")
    suggestion_section = script.split("// ── Source and free-text author suggestions", 1)[1].split(
        "// ── Category autocomplete", 1
    )[0]

    assert all(
        value in suggestion_section
        for value in (
            'querySelectorAll("input[data-suggestions-url][data-suggestions-field]").forEach',
            "let timer = null",
            "let controller = null",
            "new AbortController()",
            "controller.abort()",
            "query.length < 2",
            "clearSuggestions()",
            "if (!response.ok)",
            "requestController === controller",
            "}, 250);",
            "datalist.replaceChildren()",
            'document.createElement("option")',
            "option.value = suggestion",
            "datalist.appendChild(option)",
        )
    )
    assert "innerHTML" not in suggestion_section
