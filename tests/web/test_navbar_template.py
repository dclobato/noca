#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Rendering tests for the shared contest navbar partial.

The navbar and the contest dashboard describe a role through one shared
vocabulary. These tests pin that agreement, because the two surfaces previously
kept private copies that had already drifted apart on UBERADMIN.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from jinja2 import ChainableUndefined, Environment, FileSystemLoader

from shared.enumerations import RoleEnum
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
from web.template_globals import ROLE_BADGE_CLASSES, ROLE_ICONS, ROLE_LABELS, template_globals

# Every destination the navbar can link to, and the roles that may see it.
NAVBAR_ENDPOINTS: dict[str, set[RoleEnum]] = {
    "contest_score": set(RoleEnum),
    "contest_problems": {role for role in RoleEnum if role is not RoleEnum.USER},
    "contest_clarifications": {RoleEnum.ADMIN, RoleEnum.UBERADMIN, RoleEnum.JUDGE, RoleEnum.TEAM},
    "contest_runs": {RoleEnum.ADMIN, RoleEnum.UBERADMIN, RoleEnum.JUDGE, RoleEnum.TEAM},
    "contest_tasks": {RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.STAFF, RoleEnum.TEAM},
    "contest_reports": {RoleEnum.ADMIN, RoleEnum.UBERADMIN, RoleEnum.JUDGE},
    "contest_solution_tests": {RoleEnum.ADMIN, RoleEnum.UBERADMIN, RoleEnum.JUDGE},
    "view": {RoleEnum.ADMIN, RoleEnum.UBERADMIN},
}

_ROOT = Path(__file__).resolve().parents[2]


class _Url:
    def __init__(self, path: str) -> None:
        self.path = path


class _Request:
    """Minimal stand-in exposing the request API the navbar uses."""

    query_params: dict[str, str] = {}
    scope: dict[str, Any] = {}
    url = _Url("/somewhere-else")

    def url_for(self, name: str, **params: Any) -> str:
        """Return a stable fake URL for a route name.

        Args:
            name: The route name.
            **params: Route parameters; only ``slug`` is meaningful here.

        Returns:
            A path built from the route name and optional slug.
        """
        slug = params.get("slug")
        return f"/{name}/{slug}" if slug else f"/{name}"


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader([str(_ROOT / "web" / "template"), str(_ROOT / "shared" / "template")]),
        undefined=ChainableUndefined,
        autoescape=True,
    )
    env.globals.update(template_globals())
    env.globals["app_version"] = "test"
    env.globals["brand_name"] = "NOCA"
    return env


def _user(role: RoleEnum, user_id: str = "user-1", username: str = "someone") -> Any:
    return type(
        "_User",
        (),
        {
            "id": user_id,
            "username": username,
            "role": role.value,
            "media_cache_version": "7",
        },
    )()


def _contest(chief_judge_id: str | None = None) -> Any:
    return type(
        "_Contest",
        (),
        {
            "id": "contest-1",
            "login_slug": "slug",
            "contest_name": "South American Finals",
            "chief_judge_id": chief_judge_id,
        },
    )()


def _render(user: Any, contest: Any | None = None) -> str:
    template = _env().get_template("_partials/_navbar.html")
    return template.render(request=_Request(), current_user=user, contest=contest)


def _render_nav(
    user: Any,
    contest: Any,
    request: Any | None = None,
    contest_nav_section: str | None = None,
) -> str:
    """Render the contest navigation band, which lives below the navbar."""
    template = _env().get_template("_partials/_contest_nav.html")
    return template.render(
        request=request or _Request(),
        current_user=user,
        contest=contest,
        contest_nav_section=contest_nav_section,
    )


@pytest.mark.parametrize("role", list(RoleEnum))
def test_every_role_renders_its_own_label_icon_and_badge(role: RoleEnum) -> None:
    html = _render(_user(role), _contest())

    assert ROLE_LABELS[role.value] in html
    assert ROLE_ICONS[role.value] in html
    for badge_class in ROLE_BADGE_CLASSES[role.value].split():
        assert badge_class in html


def test_chief_judge_overrides_only_the_label() -> None:
    judge = _user(RoleEnum.JUDGE, user_id="judge-1")

    html = _render(judge, _contest(chief_judge_id="judge-1"))

    assert "Chief Judge" in html
    # The override is authority, not function: icon and colour stay the judge's.
    assert ROLE_ICONS[RoleEnum.JUDGE.value] in html
    assert ROLE_BADGE_CLASSES[RoleEnum.JUDGE.value].split()[0] in html


def test_an_ordinary_judge_is_not_labelled_chief_judge() -> None:
    html = _render(_user(RoleEnum.JUDGE, user_id="judge-2"), _contest(chief_judge_id="judge-1"))

    assert "Chief Judge" not in html
    assert ROLE_LABELS[RoleEnum.JUDGE.value] in html


@pytest.mark.parametrize("role", [role for role in RoleEnum if role is not RoleEnum.UBERADMIN])
def test_non_uberadmins_request_their_avatar(role: RoleEnum) -> None:
    html = _render(_user(role, user_id="abc"), _contest())

    assert "/user/abc/avatar?v=7" in html
    assert "shield_person" not in html


def test_uberadmins_never_request_an_avatar() -> None:
    html = _render(_user(RoleEnum.UBERADMIN, user_id="ua-1"), _contest())

    # UberAdmins have no avatar row; asking for one could only ever 404.
    assert "/user/ua-1/avatar" not in html
    assert "shield_person" in html
    assert "noca-identity-fallback" in html


def test_uberadmins_keep_their_username_visible() -> None:
    html = _render(_user(RoleEnum.UBERADMIN, username="root-two"), _contest())

    # Several UberAdmins all render the same shield glyph, so the name is what
    # tells them apart.
    assert "root-two" in html


def test_user_role_pill_keeps_its_paired_light_contrast() -> None:
    """Dark mode must not remap the background while leaving dark text behind."""
    html = _render(_user(RoleEnum.USER), _contest())

    assert "text-bg-light" in html
    assert "bg-light text-dark" not in html


def test_brand_targets_the_contest_when_there_is_one() -> None:
    html = _render(_user(RoleEnum.ADMIN), _contest())

    assert 'href="/contest_dashboard/slug"' in html


def test_brand_targets_the_uberadmin_dashboard_for_an_uberadmin_without_contest() -> None:
    html = _render(_user(RoleEnum.UBERADMIN), None)

    assert 'href="/uberadmin_dashboard"' in html


@pytest.mark.parametrize("role", [role for role in RoleEnum if role is not RoleEnum.UBERADMIN])
def test_brand_never_sends_a_non_uberadmin_to_the_uberadmin_dashboard(role: RoleEnum) -> None:
    html = _render(_user(role), None)

    assert "/uberadmin_dashboard" not in html
    assert 'href="/contests_list"' in html


def test_the_clock_appears_only_inside_a_contest() -> None:
    with_contest = _render(_user(RoleEnum.TEAM), _contest())
    without_contest = _render(_user(RoleEnum.TEAM), None)

    assert "contest-countdown" in with_contest
    assert "South American Finals" in with_contest
    assert "contest-countdown" not in without_contest


def test_the_navigation_landmark_is_named() -> None:
    html = _render(_user(RoleEnum.TEAM), _contest())

    assert 'aria-label="Contest"' in html


def test_the_non_contest_navigation_landmark_is_named_for_its_scope() -> None:
    html = _render(_user(RoleEnum.TEAM), None)

    assert 'aria-label="Primary navigation"' in html
    assert 'aria-label="Contest"' not in html


def test_navbar_endpoints_exist_in_the_real_application() -> None:
    """The navbar resolves destinations tolerantly; this is what keeps it honest.

    `nav_url` returns "" for an unmounted route so single-router test
    applications can render the bar. That would also hide a destination lost for
    real, so the full set is asserted against the actual route table.
    """
    from web.main import app

    # Starlette types the table as `BaseRoute`, which carries no `name`; only
    # the routed subclasses do, so the attribute is read defensively.
    mounted = {name for route in app.routes if (name := getattr(route, "name", None)) is not None}

    assert set(NAVBAR_ENDPOINTS) <= mounted
    assert {"contest_dashboard", "contests_list", "uberadmin_dashboard", "profile_get", "logout"} <= mounted


@pytest.mark.parametrize("role", list(RoleEnum))
def test_navigation_is_gated_exactly_like_the_dashboard(role: RoleEnum) -> None:
    html = _render_nav(_user(role), _contest())

    for endpoint, allowed_roles in NAVBAR_ENDPOINTS.items():
        if role in allowed_roles:
            assert f"/{endpoint}/slug" in html, f"{role.value} should reach {endpoint}"
        else:
            assert f"/{endpoint}/slug" not in html, f"{role.value} should not reach {endpoint}"


def test_user_navigation_only_exposes_score() -> None:
    """The USER role is a scoreboard observer, not a contest participant."""
    html = _render_nav(_user(RoleEnum.USER), _contest())

    assert html.count("noca-contest-nav-item") == 1
    assert "/contest_score/slug" in html
    assert "/contest_dashboard/slug" not in html


def test_a_chief_judge_reaches_tasks_but_an_ordinary_judge_does_not() -> None:
    chief = _render_nav(_user(RoleEnum.JUDGE, user_id="judge-1"), _contest(chief_judge_id="judge-1"))
    ordinary = _render_nav(_user(RoleEnum.JUDGE, user_id="judge-2"), _contest(chief_judge_id="judge-1"))

    assert "/contest_tasks/slug" in chief
    assert "/contest_tasks/slug" not in ordinary


@pytest.mark.parametrize("role", list(RoleEnum))
@pytest.mark.parametrize("is_designated_chief", [False, True])
def test_task_navigation_uses_the_authoritative_permission(
    role: RoleEnum,
    is_designated_chief: bool,
) -> None:
    """The template must not drift from the task service's access rule."""
    user = _user(role, user_id="candidate")
    contest = _contest(chief_judge_id="candidate" if is_designated_chief else "someone-else")

    html = _render_nav(user, contest)

    assert ("/contest_tasks/slug" in html) is can_view_tasks(user, contest)


def test_templates_receive_the_authoritative_permission_predicates() -> None:
    """Template globals expose the service functions, not copied rules."""
    globals_ = template_globals()

    assert globals_["noca_is_chief_judge"] is is_chief_judge
    assert globals_["noca_can_view_tasks"] is can_view_tasks
    assert globals_["noca_can_answer_clarifications"] is can_answer_clarifications
    assert globals_["noca_can_create_announcement"] is can_create_announcement
    assert globals_["noca_can_request_clarification"] is can_request_clarification
    assert globals_["noca_can_confirm_verdict"] is can_confirm_verdict
    assert globals_["noca_confirmation_is_decisive"] is confirmation_is_decisive


def test_the_account_dropdown_carries_no_contest_navigation() -> None:
    """Destinations live in the band below, not crammed into the bar.

    A row of small icons between the clock and the account controls is slower to
    hit and to read under contest time pressure than a band that can afford a
    large icon and a full label for each destination.
    """
    html = _render(_user(RoleEnum.ADMIN), _contest())

    assert "navbar-toggler" not in html
    for endpoint in NAVBAR_ENDPOINTS:
        assert f"/{endpoint}/slug" not in html


def test_every_destination_is_visible_with_a_label_and_none_are_hidden_in_a_menu() -> None:
    html = _render_nav(_user(RoleEnum.ADMIN), _contest())

    assert "dropdown" not in html
    for label in ("Score", "Problems", "Clarifications", "Runs", "Tasks", "Reports", "Administration"):
        assert f">{label}</span>" in html


def test_the_band_names_its_landmark() -> None:
    html = _render_nav(_user(RoleEnum.TEAM), _contest())

    assert 'aria-label="Contest sections"' in html


def _request_at(path: str) -> Any:
    class _RequestAt(_Request):
        url = _Url(path)

    return _RequestAt()


def test_the_current_destination_is_marked_active() -> None:
    html = _render_nav(_user(RoleEnum.TEAM), _contest(), request=_request_at("/contest_runs/slug"))

    assert 'aria-current="page"' in html
    assert html.count("is-current") >= 1


def test_only_the_current_destination_is_marked() -> None:
    """Administration used to light up on every page.

    Eight contest handlers are named `view`, and the endpoint function name is
    what the request scope exposes, so name matching marked the Administration
    entry on Scoreboard, Problems, Runs and everywhere else.
    """
    html = _render_nav(_user(RoleEnum.ADMIN), _contest(), request=_request_at("/contest_score/slug"))

    assert html.count('aria-current="page"') == 1
    marked = html.split('aria-current="page"')[0]
    assert marked.rsplit("href=", 1)[-1].startswith('"/contest_score/slug"')


def test_a_sub_page_keeps_its_section_marked() -> None:
    html = _render_nav(_user(RoleEnum.TEAM), _contest(), request=_request_at("/contest_problems/slug/3"))

    assert html.count('aria-current="page"') == 1
    assert "/contest_problems/slug" in html.split('aria-current="page"')[0]


def test_submission_review_marks_runs_from_its_section_hint() -> None:
    html = _render_nav(
        _user(RoleEnum.JUDGE),
        _contest(),
        request=_request_at("/c/slug/submissions/submission-1/review"),
        contest_nav_section="contest_runs",
    )

    assert html.count('aria-current="page"') == 1
    marked = html.split('aria-current="page"')[0]
    assert marked.rsplit("href=", 1)[-1].startswith('"/contest_runs/slug"')


def test_the_dashboard_does_not_match_every_page_below_it() -> None:
    """The contest root is a prefix of every other destination."""
    html = _render_nav(_user(RoleEnum.TEAM), _contest(), request=_request_at("/contest_runs/slug"))

    dashboard_chunk = html.split("Dashboard")[0]
    assert 'aria-current="page"' not in dashboard_chunk


def test_nothing_is_marked_on_an_unrelated_page() -> None:
    html = _render_nav(_user(RoleEnum.TEAM), _contest(), request=_request_at("/profile"))

    assert 'aria-current="page"' not in html


def test_the_clock_is_not_absolutely_positioned() -> None:
    """The clock used to overlap the brand and identity cluster at narrow widths."""
    html = _render(_user(RoleEnum.TEAM), _contest())

    assert "position-absolute" not in html
    assert "noca-navbar-clock" in html


def test_the_countdown_is_plain_text_in_no_container() -> None:
    """It leads on type alone; a box around it would be decoration."""
    html = _render(_user(RoleEnum.TEAM), _contest())

    assert "noca-navbar-clock-panel" not in html
    assert "badge" not in html.split('id="contest-countdown"')[0].rsplit("noca-navbar-clock", 1)[-1]


def test_the_bar_is_sticky_as_part_of_the_page_chrome() -> None:
    """The navbar, the navigation band and the breadcrumb are pinned together.

    Stickiness deliberately does not live on this partial. Bootstrap's
    `sticky-top` here would pin the navbar alone and let the other two bars
    scroll away underneath it; `_base.html` wraps all three in one sticky
    element instead, so their offsets can never disagree.
    """
    html = _render(_user(RoleEnum.TEAM), _contest())
    base = (Path(__file__).resolve().parents[2] / "web/template/_base.html").read_text(encoding="utf-8")

    assert "sticky-top" not in html
    chrome_start = base.index('<div class="noca-sticky-chrome">')
    navbar = base.index("_partials/_navbar.html", chrome_start)
    contest_nav = base.index("_partials/_contest_nav.html", navbar)
    breadcrumb = base.index('class="breadcrumb-bar', contest_nav)
    chrome_end = base.index("End .noca-sticky-chrome.", breadcrumb)
    wrapper_close = base.rindex("</div>", breadcrumb, chrome_end)
    script = base.index("sticky-chrome.js", chrome_end)
    main = base.index('<main id="main-content"', script)

    assert chrome_start < navbar < contest_nav < breadcrumb < wrapper_close < chrome_end
    assert chrome_end < script < main
    assert "defer" not in base[chrome_end:main]


def test_logout_is_a_post_not_a_link() -> None:
    """A GET logout is prefetchable and sits a few pixels from the profile link."""
    html = _render(_user(RoleEnum.TEAM), _contest())

    assert '<form method="post"' in html
    assert 'action="/logout"' in html
    assert 'href="/logout"' not in html


def test_account_controls_live_in_an_accessible_dropdown() -> None:
    html = _render(_user(RoleEnum.TEAM, username="regional-team"), _contest())

    assert 'data-bs-toggle="dropdown"' in html
    assert 'aria-expanded="false"' in html
    assert 'aria-haspopup="true"' in html
    assert 'aria-controls="noca-navbar-account-menu"' in html
    assert 'aria-labelledby="noca-navbar-account-toggle"' in html
    assert "Signed in as" in html
    assert "regional-team" in html
    assert 'href="/profile_get"' in html


def test_the_phase_pill_starts_hidden_and_is_driven_by_the_clock() -> None:
    html = _render(_user(RoleEnum.TEAM), _contest())

    assert 'id="contest-phase"' in html
    # Hidden until the clock reports a phase, so it never flashes a wrong state.
    assert "hidden" in html


def test_the_theme_control_rides_in_the_identity_cluster() -> None:
    """The preference control sits with the account controls, not in the footer.

    The id is the whole contract of `shared/static/js/theme-toggle.js`, so the
    move reuses the existing persistence rather than reimplementing it.
    """
    html = _render(_user(RoleEnum.TEAM), _contest())

    assert 'id="theme-toggle-btn"' in html
    assert 'aria-label="Switch to dark mode"' in html
    assert "noca-navbar-theme-toggle" in html

    cluster = html.index("noca-navbar-identity")
    toggle = html.index('id="theme-toggle-btn"')
    account = html.index('id="noca-navbar-account-toggle"')
    assert cluster < toggle < account


def test_the_theme_icon_carries_no_class_the_toggle_script_would_destroy() -> None:
    """`theme-toggle.js` replaces the button's innerHTML on every flip.

    A class on the glyph therefore survives exactly until the first click, which
    is how the Arena topbar icon silently lost its sizing. The navbar styles the
    glyph through the button instead.
    """
    html = _render(_user(RoleEnum.ADMIN), _contest())

    icon = html[html.index('id="theme-toggle-btn"') :]
    icon = icon[: icon.index("</button>")]
    assert '<i class="material-symbols-outlined" aria-hidden="true">dark_mode</i>' in icon


def test_the_footer_control_only_survives_where_there_is_no_navbar() -> None:
    """Exactly one `#theme-toggle-btn` per page keeps the id binding unambiguous.

    `_base.html` renders the navbar only for a signed-in user, so the footer
    control is what anonymous pages keep -- and it must not double up with the
    navbar one for everybody else.
    """
    footer = (_ROOT / "web" / "template" / "_partials" / "_footer.html").read_text(encoding="utf-8")

    guard = footer.index("{% if not current_user %}")
    toggle = footer.index('id="theme-toggle-btn"')
    end = footer.index("{% endif %}")
    assert guard < toggle < end


def test_an_unmapped_role_fails_visibly() -> None:
    """A role added without a label must not render as its raw enum value."""
    mystery = type(
        "_User",
        (),
        {"id": "u", "username": "u", "role": "zz", "media_cache_version": "1"},
    )()

    html = _render(mystery, _contest())

    assert "Unknown role" in html
    assert ">zz<" not in html


def test_no_destination_nests_under_another_except_the_dashboard() -> None:
    """Prefix matching keeps a section marked on its own sub-pages.

    That is only unambiguous while no destination sits under a sibling. The
    contest dashboard is the one exception, and it is matched exactly for
    precisely that reason.
    """
    from web.main import app

    paths = {name: str(app.url_path_for(name, slug="S")).rstrip("/") for name in NAVBAR_ENDPOINTS}
    dashboard = str(app.url_path_for("contest_dashboard", slug="S")).rstrip("/")

    nested = [
        (child, parent)
        for child, child_path in paths.items()
        for parent, parent_path in paths.items()
        if child != parent and parent_path != dashboard and child_path.startswith(parent_path + "/")
    ]

    assert nested == [], f"destinations nested under a sibling: {nested}"


def test_the_countdown_carries_no_urgency_until_the_clock_reports_one() -> None:
    """Remaining time and contest phase are two questions, so two hooks.

    The countdown element takes `data-urgency` and the phase marker keeps
    `data-phase`; neither is derived from the other, since a frozen contest can
    still have hours to run. The attribute is deliberately absent from the
    template: the bar first paints "Updating...", which is not a contest state,
    and defaulting to the resting white avoids flashing a colour the clock has
    not yet earned. What the driver then does with the hook is pinned
    behaviourally in `tests/web/js/contest-clock.test.cjs`.
    """
    html = _render(_user(RoleEnum.TEAM), _contest())

    assert 'id="contest-countdown"' in html
    assert "data-urgency" not in html


def test_urgency_colours_come_from_shared_tokens_and_rest_on_white() -> None:
    """No inline styles, no per-page hex: the states name tokens.

    White stays the default so the shift reads as an exception, and the tokens
    carry an explicit dark-ground mapping because the navbar sets
    `data-bs-theme="dark"` on itself regardless of the page theme.
    """
    root = Path(__file__).resolve().parents[2]
    chrome = (root / "web/static/css/contest/_chrome.css").read_text(encoding="utf-8")
    tokens = (root / "shared/static/css/tokens.css").read_text(encoding="utf-8")

    for state in ("warning", "critical", "ended"):
        rule = f'.noca-navbar-countdown[data-urgency="{state}"]'
        assert rule in chrome
        assert f"var(--noca-urgency-{state})" in chrome.split(rule, 1)[1].split("}", 1)[0]
        assert f"--noca-urgency-{state}:" in tokens

    dark = tokens.split('[data-bs-theme="dark"] {', 1)[1]
    assert "--noca-urgency-warning:" in dark
    assert "--noca-urgency-critical:" in dark
    # The resting state is the plain white the countdown has always been, and
    # nothing animates: colour is the only thing this change adds. `rsplit`
    # skips the narrow-viewport font-size override earlier in the file and
    # lands on the base rule.
    tail = chrome.rsplit(".noca-navbar-countdown {", 1)[1]
    base = tail.split("}", 1)[0]
    urgency_block = tail.split("/* The contest name", 1)[0]

    assert "color: #fff;" in base
    assert "animation" not in urgency_block
    assert "@keyframes" not in chrome
