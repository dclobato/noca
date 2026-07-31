#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Template and static-asset contracts for the ceremony interfaces.

Both pages are rendered through the **production** Jinja environment — base
template, static mounts, and URL helpers included — so a broken block or a
missing mount fails here rather than in front of an audience.

The control-page assertions deliberately test for a *credential*, not for the
word "secret": the page legitimately contains ``id="control-secret"`` and a
prompt label. What must never appear is a secret-bearing query parameter, a
pre-filled value, or any use of persistent browser storage.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient
from jinja2 import ChoiceLoader, FileSystemLoader
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import animator.main as animator_main
from animator.config import settings
from tests.animator._reveal_seed import Ceremony, seed_ceremony
from web.models.users import UberAdmin

_ANIMATOR_DIR = Path(animator_main.__file__).resolve().parent
_SHARED_DIR = _ANIMATOR_DIR.parent / "shared"
_STATIC_DIR = _ANIMATOR_DIR / "static"


@pytest.fixture(autouse=True)
def _control_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the deployment kill switch on for every test but the one that pins it off.

    The switch defaults to off, so without this the operator shell would answer
    ``404`` everywhere and the tests below would pass or fail on whether the
    developer's ``.env`` happens to enable control.
    """
    monkeypatch.setattr(settings, "ENABLE_CONTROL", True)


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
    return templates


def _wire_app(engine: AsyncEngine) -> FastAPI:
    """Attach a test session factory and templates to the real animator app."""
    animator_main.app.state.db_session = async_sessionmaker(engine, expire_on_commit=False)
    animator_main.app.state.templates = _build_templates()
    return animator_main.app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _get(engine: AsyncEngine, url: str) -> str:
    """Render one page through the real app and return its HTML."""
    app = _wire_app(engine)
    async with _client(app) as client:
        response = await client.get(url)
    assert response.status_code == 200, url
    return response.text


def _ceremony_url(ceremony: Ceremony, scope: str = "global") -> str:
    return f"/c/{ceremony.slug}/ceremony?scope={scope}"


# ---------------------------------------------------------------------------
# Projector shell
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_projector_wires_scope_and_media_bases(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    html = await _get(session.bind, _ceremony_url(ceremony, ceremony.site_a))  # type: ignore[arg-type]

    # The resolved canonical scope, not the raw query value.
    assert f'data-scope="{ceremony.site_a}"' in html
    assert "Campus A" in html
    for hook in [
        'id="ceremony-app"',
        'id="ceremony-standings"',
        'id="ceremony-header-row"',
        'id="ceremony-phase"',
        'id="ceremony-progress"',
        'id="ceremony-status"',
        'id="ceremony-empty"',
        'id="ceremony-team-modal"',
        'id="ceremony-team-photo"',
        'id="ceremony-photo-fallback"',
        'id="ceremony-team-audio"',
        'id="ceremony-audio-status"',
    ]:
        assert hook in html, hook
    assert 'class="animator-scoreboard ceremony-board"' in html
    # Media bases are derived from the routes, and both carry the ceremony scope
    # when the client appends it.
    assert f'data-photo-base="http://test/c/{ceremony.slug}/teams"' in html
    assert f'data-audio-base="http://test/c/{ceremony.slug}/teams"' in html
    assert f'data-meta-url="http://test/c/{ceremony.slug}/meta"' in html
    assert 'data-balloon-base="http://test/assets/balloon"' in html
    assert 'data-star-base="http://test/assets/star"' in html
    assert 'data-medal-base="http://test/assets/medal"' in html
    # No ceremony state is embedded; it is fetched.
    assert "reveal_log" not in html
    assert "frozen_submission_ids" not in html


@pytest.mark.asyncio
async def test_projector_modal_ships_no_audio_source(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    html = await _get(session.bind, _ceremony_url(ceremony))  # type: ignore[arg-type]

    audio_tag = html[html.index('id="ceremony-team-audio"') : html.index("</audio>")]
    # The clip URL is assigned per team after the modal opens, and removed on
    # close; a src here would start downloading every team's audio at page load.
    assert "src=" not in audio_tag
    assert "controls" in audio_tag
    assert 'preload="none"' in audio_tag
    assert "hidden" in audio_tag
    # Native controls remain the fallback whenever autoplay is refused.
    assert "autoplay" not in audio_tag
    photo_tag = html[html.index('id="ceremony-team-photo"') : html.index(">", html.index('id="ceremony-team-photo"'))]
    assert "src=" not in photo_tag
    assert "hidden" in photo_tag


@pytest.mark.asyncio
async def test_projector_accessibility_contracts(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    html = await _get(session.bind, _ceremony_url(ceremony))  # type: ignore[arg-type]

    assert 'role="status"' in html
    assert 'scope="col"' in html
    assert "<caption" in html
    assert 'aria-labelledby="ceremony-team-modal-label"' in html
    assert 'href="#animator-main"' in html  # skip link from the base template
    assert 'aria-label="Close"' in html


@pytest.mark.asyncio
async def test_projector_loads_only_ceremony_assets(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    html = await _get(session.bind, _ceremony_url(ceremony))  # type: ignore[arg-type]

    assert "ceremony.css" in html
    for script in [
        "cell-format.js",
        "animator-keyed-rows.js",
        "animator-render.js",
        "animator-animate.js",
        "ceremony-transport.js",
        "ceremony-render.js",
        "ceremony-modal.js",
        "ceremony.js",
    ]:
        assert script in html, script
    # Helpers precede the orchestrator; the shared cell formatter precedes the
    # renderer that reads it off window.
    assert html.index("cell-format.js") < html.index("ceremony-render.js")
    assert html.index("animator-keyed-rows.js") < html.index("animator-render.js")
    assert html.index("animator-render.js") < html.index("ceremony-render.js")
    assert html.index("animator-animate.js") < html.index("ceremony.js?")
    assert html.index("ceremony-transport.js") < html.index("ceremony.js?")
    assert html.index("ceremony-render.js") < html.index("ceremony.js?")
    assert html.index("ceremony-modal.js") < html.index("ceremony.js?")
    # The live-scoreboard client has no DOM to drive here.
    assert "animator-board.js" not in html
    assert "animator-live.js" not in html
    # Bootstrap is required: the modal and its focus restoration depend on it.
    assert "bootstrap.bundle.min.js" in html
    # Nothing inline.
    assert "<style" not in html
    assert "<script>" not in html


@pytest.mark.asyncio
async def test_projector_ceremony_boot_is_gone(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    html = await _get(session.bind, _ceremony_url(ceremony))  # type: ignore[arg-type]

    # ceremony.js replaced the Phase 13 placeholder wholesale.
    assert "ceremony-boot.js" not in html
    assert not (_STATIC_DIR / "js" / "ceremony-boot.js").exists()


# ---------------------------------------------------------------------------
# Operator shell
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_control_page_carries_every_command_url(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    html = await _get(session.bind, f"/c/{ceremony.slug}/control")  # type: ignore[arg-type]

    base = f"http://test/c/{ceremony.slug}/control"
    for attribute, url in [
        ("data-initial-scope", "global"),
        ("data-state-url", f"{base}/state"),
        ("data-start-url", f"{base}/start-reveal"),
        ("data-step-url", f"{base}/step"),
        ("data-back-url", f"{base}/back"),
        ("data-reset-url", f"{base}/reset"),
        ("data-jump-url", f"{base}/jump-team"),
        ("data-meta-url", f"http://test/c/{ceremony.slug}/meta"),
    ]:
        assert f'{attribute}="{url}"' in html, attribute


@pytest.mark.asyncio
async def test_control_page_never_carries_a_credential(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    html = await _get(session.bind, f"/c/{ceremony.slug}/control")  # type: ignore[arg-type]

    # No secret-bearing query parameter anywhere in the rendered page.
    for parameter in ["secret=", "token=", "key=", "password="]:
        assert parameter not in html, parameter
    # The input exists but is empty, private, and excluded from autofill.
    field = html[html.index('id="control-secret"') : html.index('id="control-secret"') + 400]
    assert 'type="password"' in field
    assert 'autocomplete="off"' in field
    assert "value=" not in field


@pytest.mark.asyncio
async def test_control_page_documents_its_shortcuts(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    html = await _get(session.bind, f"/c/{ceremony.slug}/control")  # type: ignore[arg-type]

    # Shortcuts exist only because they are discoverable on screen.
    assert "<kbd>" in html
    assert "Keyboard:" in html
    assert 'id="control-scope"' in html  # site selector for start-reveal
    assert 'id="control-jump-team"' in html
    assert 'id="control-back-ten"' in html
    assert 'id="control-step-ten"' in html
    assert 'id="control-start-over"' in html
    assert 'id="control-rebuild"' in html
    assert 'id="control-reset"' in html
    assert 'id="control-reload"' in html
    assert 'id="control-confirmation-modal"' in html
    assert "Back 10" in html
    assert "Step 10" in html
    for action in [
        "Start reveal",
        "Start over",
        "Rebuild state",
        "Reset to idle",
        "Reload state",
    ]:
        assert action in html
    assert "What each action does" in html
    assert "Recovery actions" in html
    assert "Do not use these actions during normal operation" in html
    assert "preserving the original frozen snapshot" in html
    assert "without changing it" in html
    assert html.index('id="control-rebuild"') < html.index('id="control-action-help-title"')
    assert html.index('id="control-reload"') < html.index('id="control-action-help-title"')
    rebuild_button = html[html.index('id="control-rebuild"') : html.index('id="control-rebuild"') + 180]
    reload_button = html[html.index('id="control-reload"') : html.index('id="control-reload"') + 180]
    assert "hidden" not in rebuild_button
    assert "hidden" not in reload_button
    assert 'role="alert"' in html
    assert "ceremony.css" in html
    assert "control.js" in html
    # The panel names teams through the same shared rule the projector uses.
    assert "cell-format.js" in html
    assert html.index("cell-format.js") < html.index("control.js")
    assert "<script>" not in html


@pytest.mark.asyncio
async def test_control_page_is_gated_like_the_api(session: AsyncSession, uberadmin: UberAdmin) -> None:
    from tests.animator._feed_seed import make_contest

    ceremony = await seed_ceremony(session, uberadmin)
    disabled = await make_contest(session, uberadmin, slug="ctl-off", animator_enabled=False)
    await session.commit()
    app = _wire_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        unknown = await client.get("/c/ctl-nope/control")
        off = await client.get(f"/c/{disabled.login_slug}/control")
        enabled = await client.get(f"/c/{ceremony.slug}/control")

    assert enabled.status_code == 200
    assert unknown.status_code == off.status_code == 404
    assert unknown.json() == off.json()


@pytest.mark.asyncio
async def test_control_page_respects_the_kill_switch(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    monkeypatch.setattr(settings, "ENABLE_CONTROL", False)
    app = _wire_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(f"/c/{ceremony.slug}/control")
        unknown = await client.get("/c/ctl-nope/control")

    # A switched-off deployment does not serve a panel whose buttons would all
    # fail, and it stays indistinguishable from an unknown contest.
    assert response.status_code == 404
    assert response.json() == unknown.json()


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------


def test_ceremony_css_covers_the_projector_contracts() -> None:
    css = (_STATIC_DIR / "css" / "ceremony.css").read_text(encoding="utf-8")
    shared_css = (_STATIC_DIR / "css" / "animator.css").read_text(encoding="utf-8")

    assert "prefers-reduced-motion" in css
    # The board scrolls in its own container so the page never scrolls sideways.
    assert ".ceremony-board-scroll" in css
    assert "overflow" in css
    root = css[css.index(".ceremony-root {") :]
    root = root[: root.index("}")]
    assert "--animator-row-motion-duration: 3s" in root
    assert "--animator-row-motion-easing: cubic-bezier(0.22, 1, 0.36, 1)" in root
    row_transition = css[css.index(".ceremony-board tbody tr {") :]
    row_transition = row_transition[: row_transition.index("}")]
    assert "will-change: transform" in row_transition
    header = shared_css[shared_css.index(".animator-scoreboard thead th {") :]
    header = header[: header.index("}")]
    assert "position: sticky" in header
    assert "background-color: var(--noca-surface-container)" in header
    assert ".ceremony-board thead th {" not in css
    assert ".ceremony-board th," not in css
    for selector in [
        ".ceremony-medal-watermark",
        ".ceremony-row--band-end",
        ".ceremony-row--focused",
        ".ceremony-cell--pending",
        ".ceremony-cell--next",
        ".ceremony-team-photo",
    ]:
        assert selector in css, selector

    # Medal color belongs only to the oversized team-cell watermark. Whole-row
    # tints would compete with the focused and pending ceremony states.
    for band in ["gold", "silver", "bronze"]:
        assert f'.ceremony-row[data-medal="{band}"]' not in css
    watermark = css[css.index(".ceremony-medal-watermark {") :]
    watermark = watermark[: watermark.index("}")]
    for declaration in [
        "position: absolute",
        "inset-inline-end: -2.8rem",
        "width: 7rem",
        "height: 7rem",
        "opacity: 0.25",
        "pointer-events: none",
    ]:
        assert declaration in watermark
    assert ".ceremony-medal-icon" not in css

    # The next cell pulses continuously so an audience knows where to look
    # before anything changes.
    assert "@keyframes ceremony-next-glow" in css
    next_rule = css[css.index(".ceremony-cell--next {") :]
    assert "animation:" in next_rule[: next_rule.index("}")]

    # ...but the glow is the one signal carried *by* motion, so reduced motion
    # must replace it with static emphasis rather than simply deleting it.
    reduced = css[css.index("prefers-reduced-motion") :]
    assert ".ceremony-cell--next" in reduced
    static_fallback = reduced[reduced.index(".ceremony-cell--next") :]
    assert "background-color" in static_fallback[: static_fallback.index("}")]
    # Real photos retain their intrinsic size but cannot exceed 75% of the
    # projector viewport. The checked-in placeholder expands to that width.
    photo_rule = css[css.index(".ceremony-team-photo") :]
    assert "75vw" in photo_rule[: photo_rule.index("}")]
    placeholder_rule = css[css.index(".ceremony-team-photo--placeholder") :]
    assert "width: 75vw" in placeholder_rule[: placeholder_rule.index("}")]
    dialog_rule = css[css.index(".ceremony-team-dialog") :]
    assert "75vw" in dialog_rule[: dialog_rule.index("}")]
    site_rule = shared_css[shared_css.index(".animator-team-secondary {") :]
    site_rule = site_rule[: site_rule.index("}")]
    assert "display: block" in site_rule
    assert ".ceremony-team-site {" not in css


def test_ceremony_scripts_avoid_innerhtml() -> None:
    js_dir = _STATIC_DIR / "js"
    for name in ["ceremony-render.js", "ceremony-modal.js", "ceremony.js", "control.js", "cell-format.js"]:
        source = (js_dir / name).read_text(encoding="utf-8")
        assert "innerHTML" not in source, name


def test_ceremony_applies_flip_row_motion() -> None:
    """The projector measures stable rows and animates them after reordering."""
    source = (_STATIC_DIR / "js" / "ceremony.js").read_text(encoding="utf-8")

    assert "AnimatorAnimate" in source
    assert "rowAnimation: animationApi.rowAnimationFromCss(tbody)" in source
    assert "duration:" not in source
    assert "rowMotion.measureRows()" in source
    assert "rowMotion.apply(null, firstTops)" in source


def test_ceremony_passes_all_problem_asset_bases_to_shared_renderer() -> None:
    """The projector wires both regular and first-solver problem images."""
    source = (_STATIC_DIR / "js" / "ceremony.js").read_text(encoding="utf-8")

    assert 'balloonBase: root.getAttribute("data-balloon-base")' in source
    assert 'starBase: root.getAttribute("data-star-base")' in source


def test_cell_semantics_have_a_single_owner() -> None:
    """Neither renderer may restate how a cell reads.

    ``attempts`` is already the count of failures *before* the solve, and a
    renderer that adjusts it silently understates every team's attempts. Both
    boards therefore route the glyph through ``cell-format.js``.
    """
    js_dir = _STATIC_DIR / "js"
    live_renderer = (js_dir / "animator-render.js").read_text(encoding="utf-8")
    reveal_renderer = (js_dir / "ceremony-render.js").read_text(encoding="utf-8")
    assert "formatCellText" in live_renderer
    assert "scoreboardRender.fillProblemCell" in reveal_renderer
    for source in [live_renderer, reveal_renderer]:
        assert "attempts - 1" not in source
        assert "attempts-1" not in source
