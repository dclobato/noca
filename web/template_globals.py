#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Template globals shared by every web surface.

A role is described by exactly three maps -- label, icon, badge classes -- and
they live here so the navbar and the contest dashboard cannot disagree. They
previously kept private copies in their own templates and had already drifted:
the navbar rendered an UberAdmin's pill ``bg-secondary`` while the dashboard
rendered the same pill ``bg-dark``, on the same page.

Templates read these through the ``role_pill`` macro in ``_macros.html``.

It also exposes the small amount of request context the navigation needs to mark
its current destination, and -- under ``role_matrix`` -- the declared access and
capability matrices from ``web/access_matrix/``, so any page that helps someone
choose a role can render them without a route passing them in.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.routing import NoMatchFound

from shared.enumerations import ALL_CONTEST_ROLES, RoleEnum
from web.access_matrix import (
    ACCESS_ACTORS,
    ACCESS_AREAS,
    ACCESS_LEGEND,
    CAPABILITY_ACTORS,
    CAPABILITY_GROUPS,
    CAPABILITY_LEGEND,
    CHIEF_JUDGE_NOTE,
    UBERADMIN_ATTRIBUTION_NOTE,
    MatrixActor,
)
from web.services.chief_judge_permissions import is_chief_judge
from web.services.clarification_service.permissions import (
    can_answer_clarifications,
    can_create_announcement,
    can_request_clarification,
)
from web.services.judging_service.permissions import (
    can_confirm_verdict,
    confirmation_is_decisive,
)
from web.services.task_service.permissions import can_view_tasks

ROLE_LABELS: dict[str, str] = {
    RoleEnum.UBERADMIN.value: "Uber Admin",
    RoleEnum.ADMIN.value: "Admin",
    RoleEnum.JUDGE.value: "Judge",
    RoleEnum.STAFF.value: "Staff",
    RoleEnum.TEAM.value: "Team",
    RoleEnum.USER.value: "User",
}

ROLE_ICONS: dict[str, str] = {
    RoleEnum.UBERADMIN.value: "shield_person",
    RoleEnum.ADMIN.value: "admin_panel_settings",
    RoleEnum.JUDGE.value: "gavel",
    RoleEnum.STAFF.value: "support_agent",
    RoleEnum.TEAM.value: "groups",
    RoleEnum.USER.value: "person",
}

# Every pill carries its own solid fill, so one class works on both the light
# dashboard band and the dark navbar chrome. UBERADMIN is the exception that
# forced this consolidation: a bare ``bg-dark`` pill disappears into the dark
# navbar, so it carries a hairline that keeps it readable on either surface.
ROLE_BADGE_CLASSES: dict[str, str] = {
    RoleEnum.UBERADMIN.value: "bg-dark text-light border border-secondary",
    RoleEnum.ADMIN.value: "bg-warning text-dark",
    RoleEnum.JUDGE.value: "bg-info text-dark",
    RoleEnum.STAFF.value: "bg-secondary",
    RoleEnum.TEAM.value: "bg-primary",
    RoleEnum.USER.value: "text-bg-light border",
}


# The role matrices talk about one actor the role vocabulary above has no entry
# for: the chief judge, which is not a role but the single JUDGE named by
# `contests.chief_judge_id`. It borrows the judge's icon and a judge-tinted badge
# with a hairline, so the two read as related without reading as identical.
MATRIX_ACTOR_LABELS: dict[str, str] = {
    **{actor.value: ROLE_LABELS[actor.value] for actor in MatrixActor if actor.role is not None},
    MatrixActor.CHIEF_JUDGE.value: "Chief Judge",
}

MATRIX_ACTOR_ICONS: dict[str, str] = {
    **{actor.value: ROLE_ICONS[actor.value] for actor in MatrixActor if actor.role is not None},
    MatrixActor.CHIEF_JUDGE.value: "gavel",
}

MATRIX_ACTOR_BADGE_CLASSES: dict[str, str] = {
    **{actor.value: ROLE_BADGE_CLASSES[actor.value] for actor in MatrixActor if actor.role is not None},
    MatrixActor.CHIEF_JUDGE.value: "bg-info text-dark border border-dark-subtle",
}


def role_matrix_context() -> dict[str, object]:
    """Return the declared access and capability matrices, ready to render.

    One global rather than a dozen, so a template that shows the matrices pulls
    in the whole vocabulary -- rows, columns, legends, and the two notes that
    explain the cells that surprise people -- without the page having to know
    which pieces it needs. The data itself lives in `web/access_matrix/`.

    Returns:
        The matrix data and its presentation vocabulary, keyed for Jinja.
    """
    return {
        "access_actors": ACCESS_ACTORS,
        "access_areas": ACCESS_AREAS,
        "access_legend": ACCESS_LEGEND,
        "capability_actors": CAPABILITY_ACTORS,
        "capability_groups": CAPABILITY_GROUPS,
        "capability_legend": CAPABILITY_LEGEND,
        "actor_labels": MATRIX_ACTOR_LABELS,
        "actor_icons": MATRIX_ACTOR_ICONS,
        "actor_badge_classes": MATRIX_ACTOR_BADGE_CLASSES,
        "uberadmin_note": UBERADMIN_ATTRIBUTION_NOTE,
        "chief_judge_note": CHIEF_JUDGE_NOTE,
    }


def is_current_destination(request: Request, url: str, exact: bool = False) -> bool:
    """Return whether `url` is the destination the request is already on.

    Matching is by path, not by endpoint function name. Eight contest handlers
    are named ``view`` -- their routes carry distinct explicit names, but the
    function name is what the request scope exposes -- so name matching lit up
    the Administration entry on Scoreboard, Problems, Runs and every other page
    with a handler of that name.

    A section also matches its own sub-pages, so opening a single problem keeps
    Problems marked. `exact` disables that for a destination that is a prefix of
    every other one, such as the contest dashboard at the contest root.

    Args:
        request: The request being rendered.
        url: The destination's URL, absolute or path-only.
        exact: Whether only the exact path counts as current.

    Returns:
        Whether the navigation entry for `url` should be marked current.
    """
    if not url:
        return False
    target = urlsplit(url).path.rstrip("/")
    current = request.url.path.rstrip("/")
    if exact or not target:
        return current == target
    return current == target or current.startswith(target + "/")


def nav_url(request: Request, endpoint: str, **params: object) -> str:
    """Return the URL for a navigation destination, or "" when it is unroutable.

    The navbar is rendered by every page, including the single-router
    applications the route tests build, where a sibling destination genuinely is
    not mounted. Resolving to an empty string lets those pages render while the
    real application still links everything; `test_navbar_endpoints_exist`
    asserts the full destination set against the real route table, so a
    destination lost in production fails a test rather than disappearing
    quietly.

    Args:
        request: The request being rendered.
        endpoint: The route name to resolve.
        **params: Path parameters for the route.

    Returns:
        The resolved URL, or an empty string when no such route is mounted.
    """
    try:
        return str(request.url_for(endpoint, **params))
    except NoMatchFound:
        return ""


def template_globals() -> dict[str, object]:
    """Return the globals every web template surface relies on.

    Returns:
        The role vocabulary plus the navigation request helpers.
    """
    return {
        "RoleEnum": RoleEnum,
        "all_contest_roles": ALL_CONTEST_ROLES,
        "role_labels": ROLE_LABELS,
        "role_icons": ROLE_ICONS,
        "role_badge_classes": ROLE_BADGE_CLASSES,
        "role_matrix": role_matrix_context(),
        "noca_is_chief_judge": is_chief_judge,
        "noca_can_view_tasks": can_view_tasks,
        "noca_can_answer_clarifications": can_answer_clarifications,
        "noca_can_create_announcement": can_create_announcement,
        "noca_can_request_clarification": can_request_clarification,
        "noca_can_confirm_verdict": can_confirm_verdict,
        "noca_confirmation_is_decisive": confirmation_is_decisive,
        "is_current_destination": is_current_destination,
        "nav_url": nav_url,
    }


def register_template_globals(templates: Jinja2Templates) -> None:
    """Register the shared globals on a template environment.

    Args:
        templates: The template environment to register the globals on.
    """
    templates.env.globals.update(template_globals())
