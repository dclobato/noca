#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""What each actor may *do* inside a contest module.

This is the declared form of what ``web/docs/ROUTES.md`` used to carry as a
markdown table under "Capability matrix". Reaching the page is the separate
axis in :mod:`web.access_matrix.access`.

Two things are worth reading before trusting a cell:

Every ``—`` in the UBERADMIN column of an *attributed* action comes from the
schema, not from a policy choice. Four columns record who did the work and all
four are foreign keys into ``users`` -- ``tasks.staff_id``,
``clarifications.judge_id``, ``human_submission_confirmations.judge_id``, and
``verdict_overrides.overridden_by``. Uberadmins have no ``users`` row, so they
may *supervise* this work (force-release, rejudge) but never *perform* it.

``enforced_by`` names the predicate that answers the capability at runtime.
Where it is ``None``, the rule lives in a route's role tuple with no shared
predicate behind it, so ``tests/web/test_access_matrix.py`` can only check that
row structurally. Adding a predicate for such a row is an improvement, not a
refactor: it is what makes the row verifiable.
"""

from __future__ import annotations

from typing import Final

from web.access_matrix.models import (
    Capability,
    CapabilityGrant,
    CapabilityGroup,
    Grant,
    MatrixActor,
)

#: The actors the capability matrix covers, in column order. ``USER`` is
#: included although the historical table omitted it: it is a selectable role on
#: the add-user form, and an explicitly empty column answers "what can a USER
#: do" far better than a missing one.
CAPABILITY_ACTORS: Final[tuple[MatrixActor, ...]] = (
    MatrixActor.UBERADMIN,
    MatrixActor.ADMIN,
    MatrixActor.CHIEF_JUDGE,
    MatrixActor.JUDGE,
    MatrixActor.STAFF,
    MatrixActor.TEAM,
    MatrixActor.USER,
)

#: The note that marks a grant as depending on the contest being under way. It
#: is a named constant because the test suite keys the contest-state axis off it:
#: a cell carrying this note must flip to denied before the contest starts, and
#: every other cell must not.
RUNNING_ONLY_NOTE: Final[str] = "running only"

_YES = CapabilityGrant(Grant.ALLOWED)
_NO = CapabilityGrant(Grant.DENIED)
_NA = CapabilityGrant(Grant.NOT_APPLICABLE)
_RUNNING_ONLY = CapabilityGrant(Grant.ALLOWED, note=RUNNING_ONLY_NOTE)
_OWN_RESOURCE = CapabilityGrant(Grant.OWN, note="tasks they created")
_OWN_LOCK = CapabilityGrant(Grant.OWN, note="lock holder")


def _grants(
    *,
    uberadmin: CapabilityGrant = _NO,
    admin: CapabilityGrant = _NO,
    chief_judge: CapabilityGrant = _NO,
    judge: CapabilityGrant = _NO,
    staff: CapabilityGrant = _NO,
    team: CapabilityGrant = _NO,
    user: CapabilityGrant = _NO,
) -> dict[MatrixActor, CapabilityGrant]:
    """Build a full grant mapping, defaulting every unnamed actor to denied.

    Denial is the default so that adding an actor to the matrix cannot silently
    grant it anything, and so each capability below states only what it permits.
    """
    return {
        MatrixActor.UBERADMIN: uberadmin,
        MatrixActor.ADMIN: admin,
        MatrixActor.CHIEF_JUDGE: chief_judge,
        MatrixActor.JUDGE: judge,
        MatrixActor.STAFF: staff,
        MatrixActor.TEAM: team,
        MatrixActor.USER: user,
    }


_CLARIFICATIONS = CapabilityGroup(
    key="clarifications",
    label="Clarifications",
    capabilities=(
        Capability(
            key="clarification_ask",
            label="Ask a clarification",
            grants=_grants(team=_RUNNING_ONLY),
            enforced_by="web.services.clarification_service.permissions.can_request_clarification",
        ),
        Capability(
            key="clarification_see_asker",
            label="See who asked (list view)",
            grants=_grants(uberadmin=_YES, admin=_YES, team=_NA),
        ),
        Capability(
            key="clarification_answer",
            label="Acquire + answer",
            grants=_grants(admin=_YES, chief_judge=_YES, judge=_YES),
            enforced_by="web.services.clarification_service.permissions.can_answer_clarifications",
        ),
        Capability(
            key="clarification_force_release",
            label="Force-release another's lock",
            grants=_grants(uberadmin=_YES, admin=_YES),
            enforced_by=("web.services.clarification_service.permissions.can_force_release_clarifications"),
        ),
        Capability(
            key="clarification_hide",
            label="Hide / unhide",
            grants=_grants(uberadmin=_YES, admin=_YES, chief_judge=_YES, judge=_YES),
        ),
        Capability(
            key="clarification_announce",
            label="Post an announcement",
            grants=_grants(admin=_YES, chief_judge=_YES, judge=_RUNNING_ONLY),
            enforced_by="web.services.clarification_service.permissions.can_create_announcement",
        ),
    ),
)

_TASKS = CapabilityGroup(
    key="tasks",
    label="Tasks (balloons, print, SOS)",
    capabilities=(
        Capability(
            key="task_create",
            label="Create (SOS / print)",
            grants=_grants(team=_YES),
        ),
        # Deliberately unbound. `can_view_tasks` answers "may open the tasks page",
        # which is the access axis, and returns True for any TEAM -- it cannot
        # express the OWN qualifier, because that lives in `list_tasks` as a
        # `Task.team_id == actor.id` filter. Naming it here would have claimed a
        # verification the binding test does not perform, since that test only
        # compares `grant.allowed`. `test_seeing_all_tasks_means_all_for_staff_and_own_for_a_team`
        # checks the row against `list_tasks` instead.
        Capability(
            key="task_see_all",
            label="See all tasks",
            grants=_grants(
                uberadmin=_YES,
                admin=_YES,
                chief_judge=_YES,
                staff=_YES,
                team=_OWN_RESOURCE,
            ),
        ),
        Capability(
            key="task_handle",
            label="Acquire + finish",
            grants=_grants(admin=_YES, chief_judge=_YES, staff=_YES),
            enforced_by="web.services.task_service.permissions.can_handle_tasks",
        ),
        Capability(
            key="task_print_source",
            label="Download PRINT source",
            grants=_grants(
                uberadmin=_YES,
                admin=_YES,
                chief_judge=_OWN_LOCK,
                staff=_OWN_LOCK,
            ),
        ),
        Capability(
            key="task_force_release",
            label="Force-release another's lock",
            grants=_grants(uberadmin=_YES, admin=_YES),
            enforced_by="web.services.task_service.permissions.can_force_release_tasks",
        ),
    ),
)

_VERDICTS = CapabilityGroup(
    key="verdicts",
    label="Verdicts",
    capabilities=(
        Capability(
            key="verdict_submit_run",
            label="Submit a run",
            grants=_grants(team=_YES),
        ),
        Capability(
            key="verdict_see_review",
            label="See the review page",
            grants=_grants(uberadmin=_YES, admin=_YES, chief_judge=_YES, judge=_YES),
        ),
        Capability(
            key="verdict_confirm",
            label="Acquire review + confirm",
            grants=_grants(admin=_YES, chief_judge=_YES, judge=_YES),
            enforced_by="web.services.judging_service.permissions.can_confirm_verdict",
        ),
        Capability(
            key="verdict_decisive",
            label="Confirmation is decisive",
            grants=_grants(admin=_YES, chief_judge=_YES),
            enforced_by="web.services.judging_service.permissions.confirmation_is_decisive",
        ),
        Capability(
            key="verdict_override",
            label="Override a final verdict",
            grants=_grants(admin=_YES, chief_judge=_YES),
            enforced_by="web.services.judging_service.permissions.can_override_verdict",
        ),
        Capability(
            key="verdict_rejudge",
            label="Rejudge a submission",
            grants=_grants(uberadmin=_YES, admin=_YES, chief_judge=_YES),
            enforced_by="web.services.judging_service.permissions.can_supervise_judgment",
        ),
        Capability(
            key="verdict_force_release_review",
            label="Force-release a review lock",
            grants=_grants(uberadmin=_YES, admin=_YES, chief_judge=_YES),
            enforced_by="web.services.judging_service.permissions.can_supervise_judgment",
        ),
    ),
)

_ADMINISTRATION = CapabilityGroup(
    key="administration",
    label="Administration",
    capabilities=(
        Capability(
            key="admin_manage",
            label="Contest / problem / user admin",
            grants=_grants(uberadmin=_YES, admin=_YES),
            enforced_by="web.services.contest_service.authorization.can_administer_contest",
        ),
        Capability(
            key="admin_assign_chief_judge",
            label="Assign the chief judge",
            grants=_grants(uberadmin=_YES, admin=_YES),
            enforced_by="web.services.contest_service.authorization.can_administer_contest",
        ),
        Capability(
            key="admin_reports",
            label="Reports",
            grants=_grants(uberadmin=_YES, admin=_YES, chief_judge=_YES, judge=_YES),
        ),
    ),
)


CAPABILITY_GROUPS: Final[tuple[CapabilityGroup, ...]] = (
    _CLARIFICATIONS,
    _TASKS,
    _VERDICTS,
    _ADMINISTRATION,
)


def all_capabilities() -> tuple[Capability, ...]:
    """Return every declared capability, flattened across its groups."""
    return tuple(cap for group in CAPABILITY_GROUPS for cap in group.capabilities)


def capability_by_key(key: str) -> Capability:
    """Return the declared capability named ``key``.

    Raises:
        KeyError: If no capability carries that key.
    """
    for capability in all_capabilities():
        if capability.key == key:
            return capability
    raise KeyError(key)


def capabilities_for_actor(actor: MatrixActor) -> tuple[Capability, ...]:
    """Return every capability ``actor`` may perform, fully or on own resources."""
    return tuple(cap for cap in all_capabilities() if cap.grant_for(actor).allowed)
