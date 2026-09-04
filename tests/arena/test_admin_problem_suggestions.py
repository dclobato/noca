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
from shared.enumerations import ArenaRole, ProblemValidatorType

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "arena" / "static" / "js" / "arena-suggest-combobox.js"
_CONTROLLER = _ROOT / "arena" / "static" / "js" / "arena-combo-listbox.js"


async def _create_problem(
    session: AsyncSession,
    *,
    owner_id: str,
    title: str,
    source: str | None = None,
    author: str | None = None,
    license: str | None = None,
) -> ArenaProblem:
    """Create a disabled problem with only fields relevant to suggestion tests."""
    return await admin_problem_service.create_problem(
        session,
        caller_id=owner_id,
        title=title,
        source=source,
        author=author,
        author_is_owner=author is None,
        license=license,
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
        validator_type=ProblemValidatorType.STANDARD,
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
        assert (await client.get("/admin/problems/suggestions?field=source&q=abc")).status_code == 401

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
        assert (await client.get("/admin/problems/suggestions?field=source&q=abc")).status_code == 403

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": editor_token},
    ) as client:
        assert (await client.get("/admin/problems/suggestions?q=abc")).status_code == 422
        assert (await client.get("/admin/problems/suggestions?field=title&q=abc")).status_code == 422
        assert (await client.get("/admin/problems/suggestions?field=license&q=abc")).status_code == 200
        assert (await client.get("/admin/problems/suggestions?field=source&q=a")).status_code == 422
        # Three characters is the shortest query a trigram index can answer, so
        # it is the route's minimum rather than a service-level empty result.
        assert (await client.get("/admin/problems/suggestions?field=source&q=ab")).status_code == 422
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
        license="Closed external license",
    )
    shared_problem = await _create_problem(
        session,
        owner_id=other_editor.id,
        title="Other editor published archive",
        source="Shared catalog",
        license="Shared license",
    )
    shared_problem.enabled = True
    await session.commit()

    async def get_for(
        token: str,
        query: str = "Archive",
        field: ProblemSuggestionField = "source",
    ) -> list[str]:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            cookies={"arena_access_token": token},
        ) as client:
            response = await client.get("/admin/problems/suggestions", params={"field": field, "q": query})
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
    assert await get_for(_login_token(app, editor), "license", "license") == ["Shared license"]
    assert await get_for(_login_token(app, admin), "license", "license") == [
        "Closed external license",
        "Shared license",
    ]


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
    license_duplicate = await _create_problem(
        session,
        owner_id=owner.id,
        title="Licensed one",
        license="CC BY-SA 4.0",
    )
    await _create_problem(
        session,
        owner_id=owner.id,
        title="Licensed two",
        license="CC BY-SA 4.0",
    )
    literal = await _create_problem(session, owner_id=owner.id, title="Literal", source="50%_off")
    license_literal = await _create_problem(
        session,
        owner_id=owner.id,
        title="Literal license",
        license="Custom%_License",
    )
    await _create_problem(session, owner_id=owner.id, title="Wildcard decoy", source="50AXoff")
    duplicate.source = "  Gamma  "
    case_variant.source, blank.source, literal.source = "gamma", " ", "  50%_off  "
    license_duplicate.license = "  CC BY-SA 4.0  "
    license_literal.license = "  Custom%_License  "
    await session.flush()

    async def scoped_suggestions(field: ProblemSuggestionField, query: str) -> list[str]:
        """Search as the test's admin owner."""
        return await _suggestions(session, field=field, query=query, caller_id=owner.id, is_admin=True)

    assert await scoped_suggestions("source", "gam") == ["Gamma", "gamma"]
    assert await scoped_suggestions("source", "50%_off") == ["50%_off"]
    assert await scoped_suggestions("author", "Owner") == ["Owner-backed Name Studio"]
    assert await scoped_suggestions("license", "by-sa") == ["CC BY-SA 4.0"]
    assert await scoped_suggestions("license", "Custom%_") == ["Custom%_License"]
    assert await scoped_suggestions("source", "  ") == []
    assert await scoped_suggestions("source", "Unlisted source") == []
    created = await _create_problem(
        session,
        owner_id=owner.id,
        title="New arbitrary values",
        source="Unlisted source",
        author="Unlisted contributor",
        license="Unlisted license",
    )
    await session.flush()
    assert (created.source, created.author, created.author_is_owner, created.license) == (
        "Unlisted source",
        "Unlisted contributor",
        False,
        "Unlisted license",
    )


@pytest.mark.asyncio
async def test_sqlite_suggestions_match_each_term_independently(session: AsyncSession) -> None:
    """Every term matches as its own substring, so partial terms and any order work.

    Full-text matching compares whole lexemes, so an unfinished term (`Loca`)
    matches nothing, and whole-query substring matching needs the typed text to
    be contiguous in the stored value -- which "2023 Loca" is not, since
    " / Fase " sits between. Matching the terms independently covers both.

    The portable path is per-term substring matching *alone*, so the exclusions
    below are exactly this branch's AND semantics. On PostgreSQL the same branch
    is one arm of a UNION and can only add candidates; the full-text and trigram
    arms keep their own reach, which is why the PostgreSQL suite demonstrates
    the exclusion with a term no arm can match.
    """
    owner = await _create_user(
        session,
        name="Term Owner",
        email="suggestions-terms@noca.invalid",
        role=ArenaRole.ARENA_ADMIN,
    )
    await _create_problem(
        session,
        owner_id=owner.id,
        title="Interif problem",
        source="VI Maratona de Programação InterIF - 2023 / Fase Local",
        author="Jorge Francisco Cutigi (IFSP, São Carlos)",
    )
    await _create_problem(
        session,
        owner_id=owner.id,
        title="Other edition",
        source="VII Maratona de Programação InterIF - 2024 / Fase Final",
    )
    await session.flush()

    async def scoped(field: ProblemSuggestionField, query: str) -> list[str]:
        """Search as the test's admin owner."""
        return await _suggestions(session, field=field, query=query, caller_id=owner.id, is_admin=True)

    interif_2023 = "VI Maratona de Programação InterIF - 2023 / Fase Local"
    cutigi = "Jorge Francisco Cutigi (IFSP, São Carlos)"

    # A partial trailing term, which no whole-lexeme or contiguous-substring
    # branch can match. Every term needs three letters or digits first, so the
    # two states before that are declined rather than answered by scanning.
    assert await scoped("source", "2023 L") == []
    assert await scoped("source", "2023 Lo") == []
    assert await scoped("source", "2023 Loc") == [interif_2023]
    assert await scoped("source", "2023 Loca") == [interif_2023]
    # Order is irrelevant, and a term may match inside a word.
    assert await scoped("source", "Loca 2023") == [interif_2023]
    assert await scoped("source", "aratona 2023") == [interif_2023]
    assert await scoped("author", "cutigi carlos") == [cutigi]
    assert await scoped("author", "utigi arlos") == [cutigi]
    # AND, not OR: one unmatched term excludes the row even when the others hit.
    # (Portable path only -- see the docstring.)
    assert await scoped("source", "2023 Final") == []
    assert await scoped("author", "cutigi birigui") == []
    # Repeated whitespace collapses rather than producing an empty term that
    # would match everything.
    assert await scoped("source", "2023   Local") == [interif_2023]
    # A term keeps treating the user's own wildcards literally, and one made of
    # characters pg_trgm discards carries no trigram at all.
    assert await scoped("source", "2023 %%%") == []
    assert await scoped("source", "---") == []
    assert await scoped("source", "²²²") == []


@pytest.mark.asyncio
async def test_form_wires_suggestions_in_create_and_edit_modes(session: AsyncSession) -> None:
    """Both forms expose the suggestion comboboxes while keeping free-text inputs."""
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
        create_response = await client.get("/admin/problems/new/standard")
        edit_response = await client.get(f"/admin/problems/{problem.id}/edit")

    for response in (create_response, edit_response):
        assert response.status_code == 200
        # A native datalist re-filters the server's answer by substring, which
        # discarded out-of-order matches; the form must render its own listbox.
        assert "<datalist" not in response.text
        assert all(
            value in response.text
            for value in (
                'aria-controls="source-suggestions"',
                'aria-controls="author-suggestions"',
                'aria-controls="license-suggestions"',
            )
        )
        assert response.text.count('data-suggestions-field="source"') == 1
        assert response.text.count('data-suggestions-field="author"') == 1
        assert response.text.count('data-suggestions-field="license"') == 1
        assert response.text.count('data-suggestions-url="http://testserver/admin/problems/suggestions"') == 3
        assert response.text.count('role="combobox"') == 4
        assert all(
            value in response.text
            for value in (
                'id="source-suggestions"',
                'id="author-suggestions"',
                'id="license-suggestions"',
            )
        )
        # Three suggestion listboxes plus the category picker, which shares the
        # same self-rendered dropdown.
        assert response.text.count('class="arena-combo-dropdown"') == 4
        assert "arena-suggest-combobox.js" in response.text


def test_suggestion_scripts_never_refilter_or_write_raw_html() -> None:
    """The client displays exactly what the server ranked, as text nodes.

    Debounce, cancellation, ARIA state, and selection are behavior, covered by
    the Node contract test in `test_combo_listbox_js.py`. What source text can
    still prove is the pair of properties that made this widget necessary: the
    client applies no filter of its own (the native `<datalist>` substring
    filter is exactly what discarded out-of-order server matches), and option
    text -- server data -- is never written as raw HTML.
    """
    for script in (_SCRIPT, _CONTROLLER):
        source = script.read_text(encoding="utf-8")
        # Assignment form only: the modules name `innerHTML` in a comment
        # explaining why they do not write to it.
        assert "innerHTML =" not in source, f"{script.name} must build options as text nodes"
        for refilter in (".includes(", ".startsWith(", ".indexOf("):
            assert refilter not in source, f"{script.name} must not filter the server's ranked answer"

    controller = _CONTROLLER.read_text(encoding="utf-8")
    # The close-cancels-pending-work contract, stated once so a refactor that
    # drops it fails here as well as in the behavior test.
    assert "cancelPending();" in controller
    assert "controller.abort();" in controller
