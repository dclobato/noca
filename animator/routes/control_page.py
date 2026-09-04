#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The operator's HTML shell for the reveal control API.

This is a **static page**, not a control operation. It carries no credential, and
it deliberately accepts none: the operator types the token into the rendered
page, and every command travels from the browser in an ``Authorization: Bearer``
header. A ``?secret=`` parameter would put the credential in the server log, the
browser history, and any ``Referer`` the page emits — so this route has no such
parameter to give.

Two properties are worth stating because they are easy to get wrong:

- **The gate is reused, not restated.** The page depends on ``ControlContest``,
  the very dependency the command API uses, so the contest gate and the
  ``NOCA_ANIMATOR_ENABLE_CONTROL`` kill switch apply here in exactly the same
  order and answer the same bare ``404``. A deployment with control switched off
  therefore does not serve a panel whose every button would fail.
- **The shell is not audited.** ``ControlContest`` only *notes* outcomes onto the
  request; the audit record is emitted by the ``ControlAuditRoute`` boundary,
  which this router deliberately does not install. Fetching a page is not a
  command attempt, and logging it as one would drown the real attempts.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from animator.dependencies import ControlContest, PublicScopeDep

router = APIRouter(prefix="/c/{slug}", tags=["animator-control-page"])


@router.get("/control", response_class=HTMLResponse, name="animator_control_page")
async def control_page(
    request: Request,
    contest: ControlContest,
    scope: PublicScopeDep,
) -> Response:
    """Render the operator control panel for one enabled contest.

    The template receives only URLs: six command endpoints, four controller-
    lease endpoints, the read-only state endpoint, and the public ``/meta``
    feed. No ceremony state and no credential is embedded.

    Args:
        request: Current request, used to build URLs and reach the templates.
        contest: The enabled contest, past both control gates.
        scope: Validated initial ceremony scope selected by the launcher.

    Returns:
        The rendered control shell.
    """
    slug = contest.login_slug
    control_base = str(request.url_for("animator_control_state", slug=slug)).removesuffix("/state")
    lease_base = f"{control_base}/controller-lease"
    return request.app.state.templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "control.html",
        {
            "slug": slug,
            "contest_name": contest.contest_name,
            "initial_scope": scope.canonical,
            "meta_url": str(request.url_for("animator_contest_meta", slug=slug)),
            "state_url": str(request.url_for("animator_control_state", slug=slug)),
            "start_url": str(request.url_for("animator_control_start", slug=slug)),
            "step_url": str(request.url_for("animator_control_step", slug=slug)),
            "back_url": str(request.url_for("animator_control_back", slug=slug)),
            "reset_url": str(request.url_for("animator_control_reset", slug=slug)),
            "jump_url": str(request.url_for("animator_control_jump_team", slug=slug)),
            "jump_pending_url": str(request.url_for("animator_control_jump_pending", slug=slug)),
            "show_media_url": str(request.url_for("animator_control_show_team_media", slug=slug)),
            "hide_media_url": str(request.url_for("animator_control_hide_team_media", slug=slug)),
            "lease_claim_url": f"{lease_base}/claim",
            "lease_heartbeat_url": f"{lease_base}/heartbeat",
            "lease_release_url": f"{lease_base}/release",
            "lease_takeover_url": f"{lease_base}/takeover",
        },
    )


__all__ = ["router"]
