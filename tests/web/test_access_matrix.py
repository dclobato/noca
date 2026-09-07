#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The declared matrices must agree with the code that actually enforces them.

`web/access_matrix/` is documentation and UI, not enforcement, so on its own it
could drift from the routes exactly the way the markdown tables it replaced did.
This module is what stops that: it builds a real actor per `MatrixActor`, calls
the predicate each capability names in `enforced_by` and the gate each access
area is guarded by, and asserts the answers match the declared cells -- in both
a running and a not-yet-started contest, so the state axis is covered too.

Rows whose `enforced_by` is `None` are gated by a route's role tuple with no
shared predicate behind them. Those are checked structurally only, and
`test_every_capability_without_a_predicate_is_known` pins the list so that
adding one is a deliberate act rather than an accident.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import ALL_CONTEST_ROLES, RoleEnum
from tests.conftest import _make_user
from web.access_matrix import (
    ACCESS_ACTORS,
    ACCESS_AREAS,
    CAPABILITY_ACTORS,
    CAPABILITY_GROUPS,
    AccessLevel,
    Grant,
    MatrixActor,
    all_capabilities,
)
from web.access_matrix.capabilities import RUNNING_ONLY_NOTE
from web.models.contest import Contest
from web.models.users import UberAdmin, User

pytestmark = pytest.mark.asyncio

Actor = User | UberAdmin


# ---------------------------------------------------------------------------
# Fixtures: one actor per MatrixActor, in a running and a pending contest
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def pending_contest(session: AsyncSession, uberadmin: UberAdmin) -> Contest:
    """A contest that has not started yet (starts in 2 h, lasts 2 h)."""
    contest = Contest(
        contest_name="Pending Contest",
        contest_url="http://pending.example.com",
        login_slug="pending-contest",
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


async def _build_actors(
    session: AsyncSession,
    contest: Contest,
    uberadmin: UberAdmin,
    prefix: str,
) -> dict[MatrixActor, Actor]:
    """Create one actor per `MatrixActor` inside `contest`.

    The chief judge is a second JUDGE-role user promoted through
    `contests.chief_judge_id`, because chief judge is not a role and cannot be
    produced by setting one.
    """
    actors: dict[MatrixActor, Actor] = {MatrixActor.UBERADMIN: uberadmin}
    for role in ALL_CONTEST_ROLES:
        actors[MatrixActor.for_role(role)] = _make_user(
            session,
            contest,
            uberadmin,
            f"{prefix}_{role.value.lower()}",
            f"{prefix} {role.value}",
            role,
        )
    chief = _make_user(
        session,
        contest,
        uberadmin,
        f"{prefix}_chief",
        f"{prefix} Chief",
        RoleEnum.JUDGE,
    )
    actors[MatrixActor.CHIEF_JUDGE] = chief
    await session.flush()
    contest.chief_judge_id = chief.id
    await session.flush()
    return actors


@pytest_asyncio.fixture
async def running_actors(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> dict[MatrixActor, Actor]:
    return await _build_actors(session, running_contest, uberadmin, "run")


@pytest_asyncio.fixture
async def pending_actors(
    session: AsyncSession, pending_contest: Contest, uberadmin: UberAdmin
) -> dict[MatrixActor, Actor]:
    return await _build_actors(session, pending_contest, uberadmin, "pend")


# ---------------------------------------------------------------------------
# Structural checks
# ---------------------------------------------------------------------------


async def test_every_access_area_declares_a_rule_for_every_actor() -> None:
    for area in ACCESS_AREAS:
        assert set(area.rules) == set(ACCESS_ACTORS), area.key


async def test_every_capability_declares_a_grant_for_every_actor() -> None:
    for capability in all_capabilities():
        assert set(capability.grants) == set(CAPABILITY_ACTORS), capability.key


async def test_every_contest_role_appears_in_both_matrices() -> None:
    """No role may be silently absent: an omitted column reads as 'no rules'."""
    for role in ALL_CONTEST_ROLES:
        actor = MatrixActor.for_role(role)
        assert actor in ACCESS_ACTORS
        assert actor in CAPABILITY_ACTORS


async def test_keys_are_unique() -> None:
    area_keys = [area.key for area in ACCESS_AREAS]
    capability_keys = [cap.key for cap in all_capabilities()]
    group_keys = [group.key for group in CAPABILITY_GROUPS]
    assert len(area_keys) == len(set(area_keys))
    assert len(capability_keys) == len(set(capability_keys))
    assert len(group_keys) == len(set(group_keys))


async def test_chief_judge_is_not_a_role() -> None:
    assert MatrixActor.CHIEF_JUDGE.role is None
    assert MatrixActor.for_role(RoleEnum.JUDGE) is MatrixActor.JUDGE
    for role in ALL_CONTEST_ROLES:
        assert MatrixActor.for_role(role).role == role


async def test_a_user_holds_no_contest_capability() -> None:
    """The USER column exists to say 'nothing', and must keep saying it."""
    for capability in all_capabilities():
        assert not capability.grant_for(MatrixActor.USER).allowed, capability.key


# ---------------------------------------------------------------------------
# Capability rows bound to their real predicate
# ---------------------------------------------------------------------------


def _resolve(dotted: str) -> Callable[..., bool]:
    module_name, _, attribute = dotted.rpartition(".")
    return getattr(importlib.import_module(module_name), attribute)  # type: ignore[no-any-return]


def _call(predicate: Callable[..., bool], actor: Actor, contest: Contest) -> bool:
    """Call a permission predicate, which takes either `(actor)` or `(actor, contest)`.

    The arity is read from the signature rather than discovered by catching
    `TypeError`, so a genuine `TypeError` raised *inside* a predicate surfaces as
    a failure instead of being mistaken for "this one takes fewer arguments" and
    silently retried with a different call.
    """
    parameter_count = len(inspect.signature(predicate).parameters)
    if parameter_count >= 2:
        return predicate(actor, contest)
    return predicate(actor)


_PREDICATE_CAPABILITIES = [cap for cap in all_capabilities() if cap.enforced_by is not None]


@pytest.mark.parametrize(
    "capability",
    _PREDICATE_CAPABILITIES,
    ids=lambda cap: cap.key,
)
@pytest.mark.parametrize("actor_kind", CAPABILITY_ACTORS, ids=lambda a: a.value)
async def test_capability_matches_its_predicate_in_a_running_contest(
    capability: Any,
    actor_kind: MatrixActor,
    running_contest: Contest,
    running_actors: dict[MatrixActor, Actor],
) -> None:
    assert capability.enforced_by is not None
    predicate = _resolve(capability.enforced_by)
    grant = capability.grant_for(actor_kind)
    actual = _call(predicate, running_actors[actor_kind], running_contest)
    assert actual is grant.allowed, (
        f"{capability.key}/{actor_kind.value}: matrix declares {grant.label!r} "
        f"but {capability.enforced_by} returned {actual}"
    )


@pytest.mark.parametrize(
    "capability",
    _PREDICATE_CAPABILITIES,
    ids=lambda cap: cap.key,
)
@pytest.mark.parametrize("actor_kind", CAPABILITY_ACTORS, ids=lambda a: a.value)
async def test_capability_matches_its_predicate_before_the_contest_starts(
    capability: Any,
    actor_kind: MatrixActor,
    pending_contest: Contest,
    pending_actors: dict[MatrixActor, Actor],
) -> None:
    """A `running only` note is the only thing that may change before the start.

    This is what pins the state axis into the declaration: a cell that claims to
    be unconditional must still answer True with the contest not yet begun.
    """
    assert capability.enforced_by is not None
    predicate = _resolve(capability.enforced_by)
    grant = capability.grant_for(actor_kind)
    expected = grant.allowed and grant.note != RUNNING_ONLY_NOTE
    actual = _call(predicate, pending_actors[actor_kind], pending_contest)
    assert actual is expected, (
        f"{capability.key}/{actor_kind.value} before the start: matrix declares "
        f"{grant.label!r} but {capability.enforced_by} returned {actual}"
    )


async def test_every_capability_without_a_predicate_is_known() -> None:
    """Pin the unverifiable rows so the gap stays visible and shrinks on purpose.

    Each key here is gated only by a route's role tuple. Giving one a shared
    predicate is an improvement; silently adding a new one is not.
    """
    assert {cap.key for cap in all_capabilities() if cap.enforced_by is None} == {
        "clarification_see_asker",
        "clarification_hide",
        "task_create",
        # Bound behaviourally instead, by
        # `test_seeing_all_tasks_means_all_for_staff_and_own_for_a_team`: the OWN
        # qualifier lives in a query filter, not in a boolean predicate.
        "task_see_all",
        "task_print_source",
        "verdict_submit_run",
        "verdict_see_review",
        "admin_reports",
    }


async def test_seeing_all_tasks_means_all_for_staff_and_own_for_a_team(
    session: AsyncSession,
    running_contest: Contest,
    running_actors: dict[MatrixActor, Actor],
    valkey_client: Any,
) -> None:
    """The `task_see_all` row's OWN qualifier, checked where it actually lives.

    No boolean predicate can express it: `can_view_tasks` is True for any TEAM,
    and the restriction is a `Task.team_id == actor.id` filter inside
    `list_tasks`. So this row is `enforced_by=None` and is bound here instead --
    otherwise "own" would be decoration that nothing verifies.
    """
    from web.services.task_service import create_sos_task, list_tasks

    mine = running_actors[MatrixActor.TEAM]
    theirs = _make_user(
        session, running_contest, running_actors[MatrixActor.UBERADMIN], "other_team", "Other", RoleEnum.TEAM
    )  # type: ignore[arg-type]
    await session.flush()

    await create_sos_task(session, running_contest, mine)  # type: ignore[arg-type]
    await create_sos_task(session, running_contest, theirs)
    await session.flush()

    capability = next(cap for cap in all_capabilities() if cap.key == "task_see_all")

    staff_views, _ = await list_tasks(session, running_contest, running_actors[MatrixActor.STAFF], valkey_client)
    team_views, _ = await list_tasks(session, running_contest, mine, valkey_client)

    # STAFF is declared a plain ALLOWED and must see both teams' tasks.
    assert capability.grant_for(MatrixActor.STAFF).grant is Grant.ALLOWED
    assert len(staff_views) == 2

    # TEAM is declared OWN and must see only its own.
    assert capability.grant_for(MatrixActor.TEAM).grant is Grant.OWN
    assert len(team_views) == 1
    assert {view.team_id for view in team_views} == {mine.id}


async def test_uberadmin_cannot_perform_attributed_work() -> None:
    """The four `users` foreign keys, restated as an assertion.

    An uberadmin has no `users` row, so every capability whose result is
    attributed to a person must deny it. This is a schema consequence; if it
    ever fails, the fix is a migration, not a matrix edit.
    """
    attributed = (
        "clarification_answer",
        "clarification_announce",
        "task_handle",
        "verdict_confirm",
        "verdict_decisive",
        "verdict_override",
    )
    for key in attributed:
        capability = next(cap for cap in all_capabilities() if cap.key == key)
        assert not capability.grant_for(MatrixActor.UBERADMIN).allowed, key


# ---------------------------------------------------------------------------
# Access areas bound to their real route gate
# ---------------------------------------------------------------------------


def _gate_allows(gate: Callable[[Actor, Contest], Any], actor: Actor, contest: Contest) -> bool:
    """Normalize the two gate shapes the routes use into one boolean.

    Some modules return "is this blocked", others raise `HTTPException(403)`.
    """
    try:
        blocked = gate(actor, contest)
    except HTTPException:
        return False
    return not bool(blocked)


def _area_gates() -> dict[str, Callable[[Actor, Contest], Any]]:
    """Map each access area to the callable the routes really guard it with."""
    score = importlib.import_module("web.routes.contest_score")
    problems = importlib.import_module("web.routes.contest_problems")
    runs = importlib.import_module("web.routes.contest_runs_helpers")
    tasks = importlib.import_module("web.routes.contest_tasks_helpers")
    clarifications = importlib.import_module("web.routes.contest_clarifications_helpers")

    allowed_clarification_roles = clarifications._ALLOWED
    allowed_runs_roles = runs._ALLOWED

    def clarification_gate(actor: Actor, contest: Contest) -> bool:
        # Clarification viewing has no contest-state gate at all: the list is
        # readable in every lifecycle state, which is precisely the rule the
        # access matrix records as `always`.
        return actor.role not in allowed_clarification_roles

    def runs_gate(actor: Actor, contest: Contest) -> bool:
        return actor.role not in allowed_runs_roles or runs._access_blocked(actor, contest)

    def tasks_gate(actor: Actor, contest: Contest) -> bool:
        # The tasks page applies two guards in sequence, and the gate is only
        # faithful if it applies both: `_ensure_task_access` raises on role and
        # on the chief-judge rule, then `_access_blocked` applies contest state.
        tasks._ensure_task_access(actor, contest)
        return tasks._access_blocked(actor, contest)

    reports = importlib.import_module("web.routes.contest_reports")
    solution_tests = importlib.import_module("web.routes.contest_solution_tests")
    team_status = importlib.import_module("web.routes.contest_team_status")
    authorization = importlib.import_module("web.services.contest_service.authorization")

    allowed_report_roles = reports._ALLOWED
    allowed_solution_test_roles = solution_tests._ALLOWED
    allowed_team_status_roles = team_status._ALLOWED

    def reports_gate(actor: Actor, contest: Contest) -> bool:
        return actor.role not in allowed_report_roles

    def solution_tests_gate(actor: Actor, contest: Contest) -> bool:
        return actor.role not in allowed_solution_test_roles

    def team_status_gate(actor: Actor, contest: Contest) -> bool:
        # No contest-state gate: the board is wanted before the gun (who has
        # arrived) as much as during the contest, so role is the only rule.
        return actor.role not in allowed_team_status_roles

    def administration_gate(actor: Actor, contest: Contest) -> bool:
        return not authorization.can_administer_contest(actor)

    return {
        "scoreboard": score._access_blocked,
        "problems": problems._check_access,
        "clarifications": clarification_gate,
        "runs": runs_gate,
        "tasks": tasks_gate,
        "solution_tests": solution_tests_gate,
        "reports": reports_gate,
        "team_status": team_status_gate,
        "administration": administration_gate,
    }


@pytest.mark.parametrize("area", ACCESS_AREAS, ids=lambda a: a.key)
@pytest.mark.parametrize("actor_kind", ACCESS_ACTORS, ids=lambda a: a.value)
async def test_access_area_matches_its_route_gate_in_a_running_contest(
    area: Any,
    actor_kind: MatrixActor,
    running_contest: Contest,
    running_actors: dict[MatrixActor, Actor],
) -> None:
    rule = area.rule_for(actor_kind)
    actual = _gate_allows(_area_gates()[area.key], running_actors[actor_kind], running_contest)
    assert actual is rule.allowed, (
        f"{area.key}/{actor_kind.value} while running: matrix declares {rule.label!r} "
        f"but the route gate returned allowed={actual}"
    )


@pytest.mark.parametrize("area", ACCESS_AREAS, ids=lambda a: a.key)
@pytest.mark.parametrize("actor_kind", ACCESS_ACTORS, ids=lambda a: a.value)
async def test_access_area_matches_its_route_gate_before_the_contest_starts(
    area: Any,
    actor_kind: MatrixActor,
    pending_contest: Contest,
    pending_actors: dict[MatrixActor, Actor],
) -> None:
    rule = area.rule_for(actor_kind)
    expected = rule.level is AccessLevel.ALWAYS
    actual = _gate_allows(_area_gates()[area.key], pending_actors[actor_kind], pending_contest)
    assert actual is expected, (
        f"{area.key}/{actor_kind.value} before the start: matrix declares {rule.label!r} "
        f"but the route gate returned allowed={actual}"
    )


async def test_a_plain_judge_reaches_no_tasks_at_all(
    running_contest: Contest,
    running_actors: dict[MatrixActor, Actor],
) -> None:
    """The cell the old markdown note blurred, now stated twice and checked.

    "chief judge only, after-start" on a single JUDGE row reads as though a judge
    reaches Tasks under a restriction. A plain judge reaches nothing there.
    """
    gate = _area_gates()["tasks"]
    assert _gate_allows(gate, running_actors[MatrixActor.CHIEF_JUDGE], running_contest)
    assert not _gate_allows(gate, running_actors[MatrixActor.JUDGE], running_contest)

    tasks = next(a for a in ACCESS_AREAS if a.key == "tasks")
    assert tasks.rule_for(MatrixActor.JUDGE).level is AccessLevel.NONE
    assert tasks.rule_for(MatrixActor.CHIEF_JUDGE).level is AccessLevel.AFTER_START


async def test_rendered_labels_match_the_documented_legend() -> None:
    """The symbols the UI prints are the ones the legend explains."""
    assert AccessLevel.ALWAYS.label == "always"
    assert AccessLevel.AFTER_START.label == "after-start"
    assert AccessLevel.NONE.label == "—"
    assert Grant.ALLOWED.label == "✓"
    assert Grant.DENIED.label == "—"
    assert Grant.OWN.label == "own"
    assert Grant.NOT_APPLICABLE.label == "n/a"
