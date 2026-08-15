#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The Arena validation-strategy chooser and the strategy-specific create routes.

A problem's strategy is immutable once stored, so the only place it is ever chosen
is the chooser's route parameter. These tests pin that down from both directions:
the chooser offers exactly the strategies that exist, and the create routes read
the strategy from the path and from nowhere a request body can reach.
"""

import pytest
from _admin_problem_app import (
    build_admin_app as _build_admin_app,
)
from _admin_problem_app import (
    create_user as _create_user,
)
from _admin_problem_app import (
    login_token as _login_token,
)
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.models.arena_problems import ArenaProblem
from shared.enumerations import ArenaRole, ProblemValidatorType


def _create_form(**overrides: str) -> dict[str, str]:
    payload = {
        "title": "Chosen Problem",
        "author_is_owner": "true",
        "source": "",
        "time_limit_ms": "1000",
        "memory_limit_kb": "262144",
        "pids_limit": "64",
        "output_limit_in_bytes": "65536",
        "problem_statement": "A statement long enough to read.",
    }
    payload.update(overrides)
    return payload


async def _editor_client(session: AsyncSession, email: str) -> tuple[AsyncClient, object]:
    app = _build_admin_app(session)
    judge = await _create_user(session, email=email, role=ArenaRole.ARENA_JUDGE, can_edit=True)
    token = _login_token(app, judge)
    client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": token},
    )
    return client, judge


# ── The chooser page ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chooser_offers_the_two_live_strategies_and_import(session: AsyncSession) -> None:
    """Standard, Interactive and Import are actionable links out of the chooser."""
    client, _ = await _editor_client(session, "chooser1@test.example")
    async with client:
        response = await client.get("/admin/problems/new")

    assert response.status_code == 200
    markup = response.text
    assert "/admin/problems/new/standard" in markup
    assert "/admin/problems/new/interactive" in markup
    assert "/admin/problems/import" in markup


@pytest.mark.asyncio
async def test_chooser_states_what_each_strategy_means(session: AsyncSession) -> None:
    """The cards carry the explanatory copy, not just a bare strategy name."""
    client, _ = await _editor_client(session, "chooser2@test.example")
    async with client:
        response = await client.get("/admin/problems/new")

    markup = response.text
    assert "compares your program's output against a fixed expected output" in markup
    assert "The problem is a conversation." in markup
    assert "your checker inspects its output" in markup


@pytest.mark.asyncio
async def test_output_checker_card_is_disabled_and_not_actionable(session: AsyncSession) -> None:
    """The reserved strategy is inert: no link, no focus, and marked disabled."""
    client, _ = await _editor_client(session, "chooser3@test.example")
    async with client:
        response = await client.get("/admin/problems/new")

    markup = response.text
    assert 'aria-disabled="true"' in markup
    assert "/admin/problems/new/checker" not in markup
    assert "Coming soon" in markup


@pytest.mark.asyncio
async def test_chooser_carries_the_list_return_state_into_the_form(session: AsyncSession) -> None:
    """Filters survive the extra hop, so Save still lands on the list they came from."""
    client, _ = await _editor_client(session, "chooser4@test.example")
    async with client:
        response = await client.get("/admin/problems/new?page=3&search=graph&language=en")

    markup = response.text
    assert "page=3" in markup
    assert "search=graph" in markup
    assert "language=en" in markup


# ── Route-parameter resolution ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_checker_redirects_to_the_chooser_rather_than_opening_an_editor(session: AsyncSession) -> None:
    """Typing the reserved URL cannot bypass the disabled card."""
    client, _ = await _editor_client(session, "chooser5@test.example")
    async with client:
        response = await client.get("/admin/problems/new/checker")

    assert response.status_code == 303
    assert response.headers["location"].startswith("http://testserver/admin/problems/new")


@pytest.mark.asyncio
async def test_unknown_strategy_is_404_not_422(session: AsyncSession) -> None:
    """The parameter is resolved in the handler, so an unknown value is a 404.

    Annotated as the enum it would be a framework 422 rendered as neutral JSON,
    which is wrong for an HTML admin page and would make an unknown strategy
    indistinguishable from the reserved one.
    """
    client, _ = await _editor_client(session, "chooser6@test.example")
    async with client:
        response = await client.get("/admin/problems/new/bogus")

    assert response.status_code == 404


# ── The strategy is stored from the path, never from the body ────────────────


@pytest.mark.asyncio
async def test_standard_path_stores_the_standard_strategy(session: AsyncSession) -> None:
    """A standard create stores STANDARD even with no validator involved."""
    client, _ = await _editor_client(session, "chooser8@test.example")
    async with client:
        response = await client.post("/admin/problems/new/standard", data=_create_form(title="Standard One"))

    assert response.status_code == 303
    problem = (await session.execute(select(ArenaProblem).where(ArenaProblem.title == "Standard One"))).scalar_one()
    assert problem.validator_type is ProblemValidatorType.STANDARD


@pytest.mark.asyncio
async def test_interactive_path_stores_interactive_without_any_validator_source(session: AsyncSession) -> None:
    """An interactive draft is allowed to start with no validator source at all.

    Judgeability is enforced at the execution gates, not at Save, which is what
    makes "choose the strategy, create, then upload the validator" possible.
    """
    client, _ = await _editor_client(session, "chooser9@test.example")
    async with client:
        response = await client.post("/admin/problems/new/interactive", data=_create_form(title="Interactive One"))

    assert response.status_code == 303
    problem = (await session.execute(select(ArenaProblem).where(ArenaProblem.title == "Interactive One"))).scalar_one()
    assert problem.validator_type is ProblemValidatorType.INTERACTIVE
    assert problem.enabled is False


@pytest.mark.asyncio
async def test_a_tampered_validator_type_field_is_never_read(session: AsyncSession) -> None:
    """A crafted body field cannot select a strategy: only the path is read."""
    client, _ = await _editor_client(session, "chooser10@test.example")
    async with client:
        response = await client.post(
            "/admin/problems/new/standard",
            data=_create_form(title="Tampered Body", validator_type="interactive"),
        )

    assert response.status_code == 303
    problem = (await session.execute(select(ArenaProblem).where(ArenaProblem.title == "Tampered Body"))).scalar_one()
    assert problem.validator_type is ProblemValidatorType.STANDARD


@pytest.mark.asyncio
async def test_the_reverse_tamper_is_also_ignored(session: AsyncSession) -> None:
    """The path wins in both directions, not just the safe one."""
    client, _ = await _editor_client(session, "chooser11@test.example")
    async with client:
        response = await client.post(
            "/admin/problems/new/interactive",
            data=_create_form(title="Reverse Tamper", validator_type="standard"),
        )

    assert response.status_code == 303
    problem = (await session.execute(select(ArenaProblem).where(ArenaProblem.title == "Reverse Tamper"))).scalar_one()
    assert problem.validator_type is ProblemValidatorType.INTERACTIVE


@pytest.mark.asyncio
async def test_a_create_never_reads_validator_fields(session: AsyncSession) -> None:
    """Creation collects the definition only, so validator fields are inert.

    A validator is staged on the judgment-data validator page, which exists only
    for a problem that already does. A crafted create body naming one is neither
    honoured nor able to upgrade the strategy: the problem is created standard,
    with no validator staged.
    """
    client, _ = await _editor_client(session, "chooser12@test.example")
    async with client:
        response = await client.post(
            "/admin/problems/new/standard",
            data=_create_form(title="Standard With Validator", validator_language_id="python-3.11"),
            files={"validator_source_file": ("validator.py", b"print('hi')", "text/x-python")},
        )

    assert response.status_code == 303
    problem = (
        await session.execute(select(ArenaProblem).where(ArenaProblem.title == "Standard With Validator"))
    ).scalar_one()
    assert problem.validator_type is ProblemValidatorType.STANDARD
    assert response.headers["location"].endswith(f"/admin/problems/{problem.id}/judgment/test-cases")


# ── The tabbed editor ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_arena_renders_no_limits_pane(session: AsyncSession) -> None:
    """Arena keeps its resource limits inside Metadata, so there is no Limits tab."""
    client, _ = await _editor_client(session, "tabs1@test.example")
    async with client:
        response = await client.get("/admin/problems/new/standard")

    markup = response.text
    assert 'data-tab-value="metadata"' in markup
    assert 'data-tab-value="statement"' in markup
    assert 'data-tab-value="statement"' in markup
    assert 'data-tab-value="limits"' not in markup
    assert 'id="time_limit_ms"' in markup


@pytest.mark.asyncio
async def test_the_standard_editor_shows_no_sample_interactions_tab(session: AsyncSession) -> None:
    """A standard problem shows sample cases, never sample interactions."""
    client, _ = await _editor_client(session, "tabs2@test.example")
    async with client:
        response = await client.get("/admin/problems/new/standard")

    assert 'data-tab-value="sample-interactions"' not in response.text


@pytest.mark.asyncio
async def test_the_editor_offers_no_way_to_change_the_strategy(session: AsyncSession) -> None:
    """The badge is inert markup: no input, no select, no hidden field."""
    client, _ = await _editor_client(session, "tabs4@test.example")
    async with client:
        response = await client.get("/admin/problems/new/interactive")

    assert 'name="validator_type"' not in response.text


@pytest.mark.asyncio
async def test_every_pane_stays_mounted_so_switching_tabs_cannot_lose_input(session: AsyncSession) -> None:
    """Bootstrap only toggles visibility; all panes are in the DOM at once."""
    client, _ = await _editor_client(session, "tabs5@test.example")
    async with client:
        response = await client.get("/admin/problems/new/standard")

    markup = response.text
    for pane in ("tab-metadata", "tab-statement"):
        assert f'id="{pane}"' in markup
    assert markup.count("tab-pane fade show active") == 1


@pytest.mark.asyncio
async def test_the_tab_strip_keeps_its_aria_semantics(session: AsyncSession) -> None:
    """Bootstrap's keyboard behaviour depends on these roles being present."""
    client, _ = await _editor_client(session, "tabs6@test.example")
    async with client:
        response = await client.get("/admin/problems/new/standard")

    markup = response.text
    assert 'role="tablist"' in markup
    assert 'role="tab"' in markup
    assert 'role="tabpanel"' in markup
    assert "noca-tab-scroll" in markup


@pytest.mark.asyncio
async def test_a_requested_tab_is_preselected(session: AsyncSession) -> None:
    """`?tab=` decides which pane opens, resolved server-side."""
    client, _ = await _editor_client(session, "tabs7@test.example")
    async with client:
        response = await client.get("/admin/problems/new/standard?tab=statement")

    assert 'data-active-tab="statement"' in response.text


@pytest.mark.asyncio
async def test_arena_ignores_a_limits_tab_it_does_not_render(session: AsyncSession) -> None:
    """Honouring it would select a pane that is not on the page."""
    client, _ = await _editor_client(session, "tabs8@test.example")
    async with client:
        response = await client.get("/admin/problems/new/standard?tab=limits")

    assert response.status_code == 200
    assert 'data-active-tab="metadata"' in response.text


@pytest.mark.asyncio
async def test_a_failed_save_opens_the_tab_that_owns_the_error(session: AsyncSession) -> None:
    """A submitted hidden pane must not override the pane containing the error."""
    client, _ = await _editor_client(session, "tabs9@test.example")
    async with client:
        response = await client.post(
            "/admin/problems/new/standard",
            data=_create_form(title="   ", active_tab="statement"),
        )

    assert response.status_code == 422
    assert 'data-active-tab="metadata"' in response.text
    assert 'id="title-server-error"' in response.text


@pytest.mark.asyncio
async def test_the_next_destination_survives_the_chooser_hop(session: AsyncSession) -> None:
    """The chooser forwards `next`; the form must receive and honour it.

    Forwarding it without accepting it made Back and Save silently ignore where
    the author came from.
    """
    client, _ = await _editor_client(session, "next1@test.example")
    async with client:
        chooser = await client.get("/admin/problems/new?next=/admin/problems%3Fpage%3D2")
        form = await client.get("/admin/problems/new/standard?next=/admin/problems%3Fpage%3D2")

    assert "next=" in chooser.text
    assert 'name="next_url"' in form.text
    assert "/admin/problems?page=2" in form.text


@pytest.mark.asyncio
async def test_an_offsite_next_is_refused(session: AsyncSession) -> None:
    """`next` is a same-origin path only, so an absolute URL must not be honoured."""
    client, _ = await _editor_client(session, "next2@test.example")
    async with client:
        form = await client.get("/admin/problems/new/standard?next=https://evil.example/x")

    assert "evil.example" not in form.text
