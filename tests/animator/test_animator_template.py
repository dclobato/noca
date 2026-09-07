#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Template, route-gate, and static-asset contract tests for the animator page.

The public scoreboard page (`GET /c/{slug}/scoreboard`) is a static shell that
fetches the `/meta` and `/snapshot` feeds client-side. These tests assert its
gate behavior, DOM hooks, accessibility contracts, and the static CSS/JS
contracts (reduced motion, horizontal overflow containment, and
`textContent`-only rendering) without a browser.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient
from jinja2 import ChoiceLoader, FileSystemLoader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import animator.main as animator_main
from animator.services.feed_cache import AnimatorFeedCache
from tests.animator._feed_seed import make_contest, seed_dataset
from web.models.site import Site
from web.models.users import UberAdmin

_ANIMATOR_DIR = Path(animator_main.__file__).resolve().parent
_SHARED_DIR = _ANIMATOR_DIR.parent / "shared"
_STATIC_DIR = _ANIMATOR_DIR / "static"


def _build_templates() -> Jinja2Templates:
    """Build a Jinja2 environment mirroring the animator runtime configuration."""
    templates = Jinja2Templates(directory=str(_ANIMATOR_DIR / "template"))
    templates.env.loader = ChoiceLoader(
        [
            FileSystemLoader(str(_ANIMATOR_DIR / "template")),
            FileSystemLoader(str(_SHARED_DIR / "template")),
        ]
    )
    templates.env.globals["app_version"] = "test"
    templates.env.globals["brand_name"] = "NOCA"
    templates.env.globals["healthmon_url"] = "https://status.example.test"
    return templates


def _wire_app(engine: AsyncEngine) -> None:
    """Attach a test session factory and templates to the real animator app.

    The real ``animator.main.app`` already registers the public router and every
    static mount referenced by ``_base.html`` (so ``request.url_for`` resolves).
    httpx's ASGITransport does not run the lifespan, so state is set here.
    """
    animator_main.app.state.feed_cache = AnimatorFeedCache()
    animator_main.app.state.db_session = async_sessionmaker(engine, expire_on_commit=False)
    animator_main.app.state.templates = _build_templates()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=animator_main.app), base_url="http://test")


def _assert_vendor_footer_links_absent(html: str) -> None:
    """Assert that the footer does not advertise implementation vendors."""
    for footer_link in ("https://fastapi.tiangolo.com/", "https://python.org/"):
        assert footer_link not in html


@pytest.mark.asyncio
async def test_page_enabled_renders_dom_hooks(session: AsyncSession, uberadmin: UberAdmin) -> None:
    contest = await make_contest(session, uberadmin, slug="page-enabled")
    await seed_dataset(session, contest, uberadmin, teams=2, problems=2, submissions=2)
    await session.commit()

    _wire_app(session.bind)  # type: ignore[arg-type]
    async with _client() as client:
        response = await client.get("/c/page-enabled/scoreboard")

    assert response.status_code == 200
    html = response.text
    # Stable DOM hooks the frontend depends on.
    for hook in [
        'id="animator-app"',
        'id="animator-contest-title"',
        'id="animator-contest-state"',
        'id="animator-contest-ended"',
        'id="animator-timer"',
        'id="animator-connection"',
        'id="animator-connection-label"',
        'id="animator-connection-elapsed"',
        'id="animator-events"',
        'id="animator-events-list"',
        'id="animator-loading"',
        'id="animator-error"',
        'id="animator-retry"',
        'id="animator-empty"',
        'id="animator-not-started"',
        'id="animator-problem-header"',
        'id="animator-standings"',
        'id="team-media-modal"',
        'id="team-media-photo"',
        'id="team-media-photo-fallback"',
    ]:
        assert hook in html
    assert 'id="animator-contest-ended"' in html
    assert 'data-state="ended"' in html
    assert "hidden>Ended</span>" in html

    # Feed URLs plus the live-stream URL and poll-fallback config are wired via
    # data attributes (Phase 08 adds the events URL and poll fallback).
    assert 'data-meta-url="http://test/c/page-enabled/meta"' in html
    assert 'data-snapshot-url="http://test/c/page-enabled/snapshot?scope=global"' in html
    assert 'data-events-url="http://test/c/page-enabled/events"' in html
    assert 'data-scope="global"' in html
    assert 'data-photo-base="http://test/c/page-enabled/teams"' in html
    assert 'data-poll-fallback="15"' in html
    assert 'id="team-media-audio"' not in html
    assert 'id="team-media-audio-status"' not in html
    assert "data-audio-base" not in html
    # The asset mount bases let the client build per-problem balloon/star <img>
    # URLs served by the animator's own /assets route.
    assert 'data-balloon-base="http://test/assets/balloon"' in html
    # The medal base drives the podium watermark, which the live board renders
    # from the same primitives the reveal projector uses.
    assert 'data-medal-base="http://test/assets/medal"' in html
    # State changes are politely announced; elapsed seconds remain non-blocking.
    assert 'id="animator-connection-label"' in html
    assert 'id="animator-connection-elapsed"' in html
    assert 'role="timer"' in html
    assert 'aria-live="off"' in html
    assert 'aria-label="Connection disruption duration: 00:00"' in html
    assert html.index('id="animator-pending"') < html.index('id="animator-events"')
    assert html.index('id="animator-events"') < html.index('id="animator-scoreboard"')


@pytest.mark.asyncio
async def test_site_page_wires_canonical_scope_to_team_photo(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A site scoreboard uses the validated scope for its team-photo requests."""
    contest = await make_contest(session, uberadmin, slug="page-site-media")
    await seed_dataset(session, contest, uberadmin, teams=1, problems=1, submissions=1)
    await session.commit()
    site_id = (await session.execute(select(Site.id).where(Site.contest_id == contest.id))).scalar_one()

    _wire_app(session.bind)  # type: ignore[arg-type]
    async with _client() as client:
        response = await client.get(f"/c/page-site-media/scoreboard?scope={site_id}")

    assert response.status_code == 200
    assert f'data-scope="{site_id}"' in response.text
    assert 'data-photo-base="http://test/c/page-site-media/teams"' in response.text


@pytest.mark.asyncio
async def test_page_accessibility_contracts(session: AsyncSession, uberadmin: UberAdmin) -> None:
    contest = await make_contest(session, uberadmin, slug="page-a11y")
    await seed_dataset(session, contest, uberadmin, teams=1, problems=1, submissions=1)
    await session.commit()

    _wire_app(session.bind)  # type: ignore[arg-type]
    async with _client() as client:
        response = await client.get("/c/page-a11y/scoreboard")

    html = response.text
    assert 'role="status"' in html  # loading region
    assert 'role="alert"' in html  # error region
    assert 'scope="col"' in html  # table column headers
    assert "<caption" in html  # accessible table caption
    assert 'href="#animator-main"' in html  # skip link target


@pytest.mark.asyncio
async def test_page_loads_shared_and_module_assets(session: AsyncSession, uberadmin: UberAdmin) -> None:
    await make_contest(session, uberadmin, slug="page-assets")
    await session.commit()

    _wire_app(session.bind)  # type: ignore[arg-type]
    async with _client() as client:
        response = await client.get("/c/page-assets/scoreboard")

    html = response.text
    assert "bootstrap.min.css" in html
    assert "noca-fonts.css" in html
    assert "animator.css" in html
    assert "animator-scoreboard.css" in html
    assert "animator-events.css" in html
    assert "animator.js" in html
    assert "animator-keyed-rows.js" in html
    assert "animator-team-cell.js" in html
    assert "animator-render.js" in html
    assert "animator-connection-status.js" in html
    assert "animator-events.js" in html
    assert "team-media-modal.js" in html
    assert "animator-theme-init.js" in html
    assert "bootstrap.bundle.min.js" in html
    assert "theme-toggle.js" in html
    assert 'id="theme-toggle-btn"' in html
    assert "https://status.example.test" in html
    assert "&copy; 2026 NOCA &mdash; vtest" in html
    assert "/static/img/ifsp.svg" in html
    for footer_link in [
        "https://sjp.ifsp.edu.br/",
        "https://x.com/dclobato",
        "https://github.com/dclobato/noca",
    ]:
        assert footer_link in html
    _assert_vendor_footer_links_absent(html)
    assert 'aria-label="Switch to dark mode"' in html
    assert 'title="Switch to dark mode"' in html


@pytest.mark.asyncio
async def test_page_empty_contest_renders(session: AsyncSession, uberadmin: UberAdmin) -> None:
    # An enabled contest with no teams still renders the shell (the empty state
    # is resolved client-side from an empty snapshot).
    await make_contest(session, uberadmin, slug="page-empty")
    await session.commit()

    _wire_app(session.bind)  # type: ignore[arg-type]
    async with _client() as client:
        response = await client.get("/c/page-empty/scoreboard")

    assert response.status_code == 200
    assert 'id="animator-empty"' in response.text


@pytest.mark.asyncio
async def test_page_missing_and_disabled_are_indistinguishable(session: AsyncSession, uberadmin: UberAdmin) -> None:
    await make_contest(session, uberadmin, slug="page-disabled", animator_enabled=False)
    await session.commit()

    _wire_app(session.bind)  # type: ignore[arg-type]
    async with _client() as client:
        disabled = await client.get("/c/page-disabled/scoreboard")
        missing = await client.get("/c/page-unknown/scoreboard")

    assert disabled.status_code == missing.status_code == 404
    assert disabled.json() == missing.json()


@pytest.mark.asyncio
async def test_page_slug_is_escaped(session: AsyncSession, uberadmin: UberAdmin) -> None:
    # Autoescaping keeps a slug-derived URL from breaking out of the attribute.
    # The router only ever resolves a real contest, but the template must escape
    # whatever slug reaches it.
    contest = await make_contest(session, uberadmin, slug="page-safe")
    await session.commit()

    _wire_app(session.bind)  # type: ignore[arg-type]
    async with _client() as client:
        response = await client.get("/c/page-safe/scoreboard")

    # url_for output is HTML-attribute escaped by Jinja autoescape.
    assert "<script>alert" not in response.text
    assert contest.login_slug in response.text


def test_pre_start_banner_reuses_the_ceremony_banner_style() -> None:
    """The pre-start banner shares one rule with the ceremony's own banner.

    Both surfaces say "there is nothing to show yet" on a projected screen, so
    they must look identical. The rule therefore lives in animator.css -- loaded
    on every animator page -- and ceremony.css must not carry a copy free to
    drift from it.
    """
    shared_css = (_STATIC_DIR / "css" / "animator.css").read_text(encoding="utf-8")
    ceremony_css = (_STATIC_DIR / "css" / "ceremony.css").read_text(encoding="utf-8")
    scoreboard_html = (_ANIMATOR_DIR / "template" / "animator.html").read_text(encoding="utf-8")
    ceremony_html = (_ANIMATOR_DIR / "template" / "ceremony.html").read_text(encoding="utf-8")

    assert ".ceremony-empty {" in shared_css
    assert ".ceremony-empty {" not in ceremony_css
    assert 'class="ceremony-empty"' in scoreboard_html
    assert 'class="ceremony-empty"' in ceremony_html

    # Moving the rule out of ceremony.css is only safe because animator.css
    # reaches the ceremony page too. Assert that reachability rather than
    # assuming it: if _base.html ever stopped loading animator.css, or the
    # ceremony stopped extending it, the banner would silently render unstyled
    # and nothing else in the suite would notice.
    base_html = (_ANIMATOR_DIR / "template" / "_base.html").read_text(encoding="utf-8")
    assert "animator.css" in base_html
    for template in (scoreboard_html, ceremony_html):
        assert '{% extends "_base.html" %}' in template


def test_static_js_uses_textcontent_not_innerhtml() -> None:
    js_dir = _STATIC_DIR / "js"
    render_js = (js_dir / "animator-render.js").read_text(encoding="utf-8")
    assert "textContent" in render_js
    # Server-provided labels must never be injected as HTML, in any animator file.
    for name in [
        "animator-render.js",
        "animator-team-cell.js",
        "animator.js",
        "animator-diff.js",
        "animator-animate.js",
        "animator-live.js",
        "animator-board.js",
        "animator-pending.js",
        "animator-connection-status.js",
        "animator-events.js",
        "team-media-modal.js",
    ]:
        assert "innerHTML" not in (js_dir / name).read_text(encoding="utf-8"), name
    # Per-problem balloon colors use an SVG fill attribute, not an inline style
    # assignment (no element.style writes in the renderer). The animator-animate
    # FLIP helper does write `.style.transform` deliberately, which is the one
    # sanctioned inline-style use (a transform-only rank transition).
    assert ".style" not in render_js


def test_static_js_helpers_load_before_orchestrator() -> None:
    base = (_ANIMATOR_DIR / "template" / "_base.html").read_text(encoding="utf-8")
    order = [
        base.index("animator-keyed-rows.js"),
        base.index("animator-team-cell.js"),
        base.index("animator-render.js"),
        base.index("animator-diff.js"),
        base.index("animator-animate.js"),
        base.index("animator-live.js"),
        base.index("animator-board.js"),
        base.index("animator-pending.js"),
        base.index("animator-connection-status.js"),
        base.index("animator-events.js"),
        base.index("team-media-modal.js"),
        base.index("animator.js"),  # only the orchestrator matches this exact substring
    ]
    # Every helper module is included, strictly before the orchestrator.
    assert order == sorted(order), "helper scripts must precede animator.js"


def test_every_animator_page_uses_the_shared_footer_shell() -> None:
    template_dir = _ANIMATOR_DIR / "template"
    base = (template_dir / "_base.html").read_text(encoding="utf-8")

    assert '{% include "_partials/_footer.html" %}' in base
    assert "theme-toggle.js" in base
    for template_name in [
        "animator.html",
        "contest_index.html",
        "control.html",
        "ceremony.html",
    ]:
        template = (template_dir / template_name).read_text(encoding="utf-8")
        assert '{% extends "_base.html" %}' in template, template_name


def _shell_css() -> str:
    """Page shell, chrome, and reduced-motion rules."""
    return (_STATIC_DIR / "css" / "animator.css").read_text(encoding="utf-8")


def _board_css() -> str:
    """Scoreboard table, problem cells, live transitions, and medal bands.

    Split out of ``animator.css`` when that file outgrew the project's soft
    size bound; both are loaded from ``_base.html`` on every animator page.
    """
    return (_STATIC_DIR / "css" / "animator-scoreboard.css").read_text(encoding="utf-8")


def test_static_css_reduced_motion_and_overflow() -> None:
    css = _shell_css()
    board = _board_css()
    events_css = (_STATIC_DIR / "css" / "animator-events.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css
    assert "prefers-reduced-motion" in events_css
    assert "animation-play-state: paused" in events_css
    # The bounded board scrolls in both directions, giving the sticky header a
    # vertical scroll container instead of scrolling the whole page.
    assert ".animator-board-scroll" in board
    assert ".animator-scoreboard-root" in css
    assert "height: 100dvh" in _rule_body(css, ".animator-body")
    assert "height: auto" in _rule_body(css, ".animator-scoreboard-root")
    assert "overflow: hidden" in _rule_body(css, ".animator-scoreboard-root")
    board_scroll = _rule_body(board, ".animator-board-scroll")
    assert "overflow: auto" in board_scroll
    assert "min-height: 0" in board_scroll


def test_scoreboard_header_is_sticky_and_opaque() -> None:
    css = _board_css()
    header = _rule_body(css, ".animator-scoreboard thead th")

    assert "position: sticky" in header
    assert "top: 0" in header
    assert "background-color: var(--noca-surface-container)" in header


def test_problem_columns_share_one_fixed_width_and_solved_cells_carry_their_balloon() -> None:
    css = _board_css()
    header = _rule_body(css, ".animator-problem-col")
    cell = _rule_body(css, ".animator-cell")

    for declaration in [
        "width: var(--animator-problem-column-width)",
        "min-width: var(--animator-problem-column-width)",
        "max-width: var(--animator-problem-column-width)",
    ]:
        assert declaration in header
        assert declaration in cell
    # A solved cell is tinted with the problem's own balloon colour, not with a
    # semantic green: what it reports is which balloon the team won. The fallback
    # covers a problem whose stored colour failed validation, and the mix is
    # against a theme-scoped surface so no dark override is needed.
    solved = _rule_body(css, ".animator-cell--solved")
    assert "var(--noca-cell-balloon, var(--noca-success))" in solved
    assert "var(--noca-surface)" in solved
    # An unsolved attempt has no balloon to name, so there the colour is the
    # semantics -- and it stays weaker than the solved tint.
    assert "var(--noca-danger)" in _rule_body(css, ".animator-cell--attempted")


def test_team_site_typography_is_explicit_and_shared() -> None:
    css = _board_css()
    secondary = _rule_body(css, ".animator-team-secondary")

    assert "font-size: 0.8em" in secondary
    assert "font-family: inherit" in secondary
    assert "font-weight: 400" in secondary


def test_static_css_transient_highlights_are_static_not_motion() -> None:
    css = _shell_css()
    board = _board_css()
    # All six transient classes exist so reduced-motion viewers still see change.
    for cls in [
        ".animator-row--rank-up",
        ".animator-row--rank-down",
        ".animator-cell--flash-solved",
        ".animator-cell--flash-attempts",
        ".animator-cell--flash-pending",
        ".animator-cell--flash-first",
    ]:
        assert cls in board, cls
    # The highlights are expressed as static outline/box-shadow (not @keyframes),
    # so the reduced-motion block — which neutralizes animation/transition — leaves
    # the state highlight intact while removing the FLIP transform motion.
    reduced = css[css.index("prefers-reduced-motion") :]
    assert "animation: none" in reduced
    assert "transition: none" in reduced


def test_row_movement_and_cell_highlights_use_independent_timings() -> None:
    root = _rule_body(_shell_css(), ".animator-root")
    board = _board_css()
    row = _rule_body(board, ".animator-scoreboard tbody tr")
    cell = _rule_body(board, ".animator-cell")

    assert "--animator-row-motion-duration: 5s" in root
    assert "--animator-row-motion-easing: cubic-bezier(0.65, 0, 0.35, 1)" in root
    assert "transition-duration: var(--animator-row-motion-duration), 0.45s, 0.45s" in row
    assert "transition-timing-function: var(--animator-row-motion-easing), ease, ease" in row
    assert "transition-duration: 0.45s" in cell


def test_flash_first_is_visually_distinct_from_persistent_first() -> None:
    css = _board_css()
    # The persistent first-solve marker and the transient first-balloon-change
    # flash must not render identically, or the transient class would be a no-op.
    persistent = _rule_body(css, ".animator-cell--first")
    transient = _rule_body(css, ".animator-cell--flash-first")
    assert persistent and transient
    assert persistent != transient, "transient first-balloon flash must differ from the persistent marker"


def _rule_body(css: str, selector: str) -> str:
    """Return the declaration block for an exact single-selector rule."""
    needle = selector + " {"
    start = css.index(needle) + len(needle)
    end = css.index("}", start)
    return css[start:end].strip()


def test_an_ended_contest_watches_for_its_release_instead_of_streaming() -> None:
    """Do not hold a live stream open for a contest that has nothing to stream.

    Releasing a scoreboard publishes no event -- ``release_scoreboard`` writes a
    flag, audits it and pre-warms a cache -- ``timer_tick`` deliberately triggers
    no refetch, and once the pre-end judging queue drains no verdict fires
    either. An SSE connection left open on an ended, still-frozen board would sit
    there delivering nothing under a badge reading "Live", and the release would
    never arrive: a projector would keep showing frozen standings until somebody
    reloaded it.
    """
    js_dir = _STATIC_DIR / "js"
    app_js = (js_dir / "animator.js").read_text(encoding="utf-8")
    status_js = (js_dir / "animator-connection-status.js").read_text(encoding="utf-8")

    # Three end states, not two: released final, ended-but-frozen, and streaming.
    assert "watchForRelease" in app_js
    assert "cancelReleaseWatch" in app_js
    # The ended branch must not start the live transport.
    ended_branch = app_js[
        app_js.index("} else if (ended) {") : app_js.index("} else {", app_js.index("} else if (ended) {"))
    ]
    assert "watchForRelease" in ended_branch
    assert "startLive" not in ended_branch

    # A reload or retry must not leave a previous watch polling forever.
    # Matched on the prefix, not the full parameter list: this assertion is about
    # what load() cancels, and re-pinning it on every new parameter only breaks
    # the test without checking anything more.
    load_body = app_js[app_js.index("function load(refs, timer, board, appliers, onSubmission, connectionStatus") :]
    assert "cancelReleaseWatch();" in load_body[: load_body.index("Promise.all")]

    # The badge says what is actually happening, and "waiting" is not degraded:
    # nothing is wrong, so it must not start the outage clock.
    assert '"Waiting for results"' in status_js
    degraded = status_js[
        status_js.index("var DEGRADED_STATUSES = {") : status_js.index(
            "};", status_js.index("var DEGRADED_STATUSES = {")
        )
    ]
    assert "waiting" not in degraded
