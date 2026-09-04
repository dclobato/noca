#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Smoke test for the Arena difficulty/rating help page.

Renders ``/help/rating`` against the real template through the actual help
router so a Jinja/KaTeX typo or a missing template variable is caught. Exercised
as a logged-out guest, since the page is on the Arena public allowlist.
"""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.routes.help import router as arena_help_router
from arena.services.token_service import ArenaTokenAction
from shared.db_schema import languages
from shared.enumerations import VERDICT_BADGE_CLASSES, VERDICT_PRIORITY, ArenaExpectedDifficulty
from shared.language_registry import default_language_seed_rows
from shared.services.arena_difficulty_display import MIN_ATTEMPTS_FOR_DISPLAY
from shared.services.arena_difficulty_histogram import BIN_COUNT, persist_difficulty_histogram
from shared.services.arena_rating import prior_solve_rate_for_difficulty
from tests.arena.conftest import install_arena_templates, mount_arena_base_routes

TEST_JWT_SECRET = "test-secret-key-for-help-rating-tests-32b!!"
HELP_CSS = Path(__file__).resolve().parents[2] / "arena" / "static" / "css" / "arena" / "_help.css"

# Named routes that ``_base.html`` resolves via ``url_for`` for a guest render.
# The help router itself supplies ``arena_help_rating`` / ``arena_help_languages``,
# and the avatar/presence endpoints live behind ``{% if current_user %}`` so a
# logged-out render never reaches them.
_NAV_ROUTE_NAMES = (
    "arena_admin_affiliation_list",
    "arena_admin_category_list",
    "arena_admin_dashboard",
    "arena_admin_dashboard_ai_usage",
    "arena_admin_dashboard_login_history",
    "arena_admin_dashboard_security_events",
    "arena_admin_dashboard_service_status",
    "arena_admin_dashboard_submissions",
    "arena_admin_dashboard_terms",
    "arena_admin_problem_list",
    "arena_admin_user_list",
    "arena_classes_index",
    "arena_classes_manage",
    "arena_classes_open",
    "arena_classes_registered",
    "arena_dashboard",
    "arena_login",
    "arena_live",
    "arena_logout",
    "arena_notifications_list",
    "arena_problem_list",
    "arena_privacy_policy",
    "arena_ranking_affiliations",
    "arena_ranking_index",
    "arena_ranking_users",
    "arena_signup",
    "arena_status",
    "arena_terms_of_service",
    "arena_user_profile",
)


def _build_help_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena app that can render the real help_rating template."""
    app = FastAPI()
    app.add_middleware(ArenaAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    templates = install_arena_templates(app)
    templates.env.filters["fmt_shell_cmd"] = lambda cmd: "" if not cmd else " ".join(cmd)
    mount_arena_base_routes(app)
    app.state.arena_db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    app.state.jwt_service = JWTService(
        config=load_token_config_from_dict(
            {
                "SECRET_KEY": TEST_JWT_SECRET,
                "JWTSERVICE_ALGORITHM": "HS256",
                "JWTSERVICE_ISSUER": "noca-arena-test",
            }
        ),
        logger=logging.getLogger(__name__),
        action_enum=ArenaTokenAction,
    )

    for name in _NAV_ROUTE_NAMES:

        async def _stub() -> Response:
            return Response("stub")

        app.add_api_route(f"/__stub__/{name}", _stub, name=name)

    app.include_router(arena_help_router)
    return app


def test_help_section_index_css_contract() -> None:
    """The section index is a scrollable strip on narrow screens, a column on wide ones."""
    css = HELP_CSS.read_text(encoding="utf-8")
    rule = re.search(r"\.arena-help-index-list\s*\{(?P<body>[^}]*)\}", css)

    assert rule is not None
    assert "flex-direction: row" in rule.group("body")
    assert "overflow-x: auto" in rule.group("body")
    # The wide-viewport override turns the same strip into a vertical column.
    assert re.search(r"@media \(min-width: 62rem\)\s*\{\s*\.arena-help-index-list\s*\{[^}]*column", css)


def test_help_tab_pattern_is_retired() -> None:
    """Tabs are gone from the help surface, styles included.

    They hid two thirds of a documentation page from in-page search, print and
    deep links; a leftover rule would invite them back.
    """
    css = HELP_CSS.read_text(encoding="utf-8")

    assert "arena-help-tabs" not in css
    assert not (Path(__file__).resolve().parents[2] / "arena" / "static" / "js" / "help-languages-tabs.js").exists()


@pytest.mark.asyncio
async def test_help_rating_renders_for_guest(session: AsyncSession) -> None:
    """The rating help page renders (200) and documents the per-problem pivot ramp."""
    app = _build_help_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/help/rating", headers={"Accept": "text/html"})

    assert response.status_code == 200
    body = response.text
    # Step 4 must describe the attempt-gated effective pivot introduced by Option 3.
    assert "m_{\\text{eff}}" in body
    assert "N_p" in body
    # The blend scale constant is surfaced from the rating service into the page.
    assert "pivot" in body.lower()
    # Every section is present in one document and reachable from the index.
    assert 'id="difficulty"' in body
    assert 'id="confidence"' in body
    assert 'href="#affiliation"' in body
    # The derivations disclose; the plain answer above them never does.
    assert "arena-help-detail-summary" in body
    assert "nav-tabs" not in body
    # The evidence threshold and the three display states are documented.
    assert f"{MIN_ATTEMPTS_FOR_DISPLAY} people" in body
    assert "7.0?" in body
    assert "Not enough data yet" in body
    # The author-estimate anchors are tabulated from the same function the worker uses.
    for level in ArenaExpectedDifficulty:
        assert level.label in body
    assert f"{prior_solve_rate_for_difficulty(ArenaExpectedDifficulty.HARD.value):.2f}" in body


@pytest.mark.asyncio
async def test_help_languages_renders_stdout_flush_hints(session: AsyncSession) -> None:
    """The languages help page renders per-language stdout flush guidance."""
    python_row = next(row for row in default_language_seed_rows() if row["id"] == "python3")
    await session.execute(languages.insert().values(python_row))
    await session.commit()

    app = _build_help_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/help/languages", headers={"Accept": "text/html"})

    assert response.status_code == 200
    body = response.text
    assert '<pre class="arena-help-cmd">print(..., flush=True)</pre>' in body
    assert '<pre class="arena-help-cmd">sys.stdout.flush()</pre>' in body
    assert "`print(..., flush=True)`" not in body
    # The interactive warning points at the roster on the same page, not at a tab.
    assert 'href="#languages"' in body
    assert 'id="interactive"' in body
    assert "nav-tabs" not in body
    assert "/static/js/help-languages-tabs.js" not in body


@pytest.mark.asyncio
async def test_help_languages_anchors_every_verdict(session: AsyncSession) -> None:
    """Each verdict has its own address so a submission page can link to exactly one."""
    app = _build_help_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/help/languages", headers={"Accept": "text/html"})

    assert response.status_code == 200
    body = response.text
    for verdict_value in VERDICT_BADGE_CLASSES:
        assert f'id="verdict-{verdict_value}"' in body
    # The mark carries a semantic tone derived from the shared badge map.
    assert 'data-verdict-tone="ok"' in body
    assert 'data-verdict-tone="bad"' in body
    assert 'data-verdict-tone="limit"' in body


@pytest.mark.asyncio
async def test_help_languages_lists_verdicts_by_judge_severity(session: AsyncSession) -> None:
    """The key is ordered by the judge's own aggregation precedence, not by hand.

    The page states that the most severe outcome is the one reported, so the list
    has to read in that order for the claim to be demonstrable -- with Accepted
    last, because it only survives when nothing else happened.
    """
    app = _build_help_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/help/languages", headers={"Accept": "text/html"})

    assert response.status_code == 200
    body = response.text
    positions = [body.index(f'id="verdict-{verdict.value}"') for verdict in VERDICT_PRIORITY]

    assert positions == sorted(positions)
    assert VERDICT_PRIORITY[-1].value == "AC"


@pytest.mark.asyncio
async def test_difficulty_distribution_empty_before_first_cycle(session: AsyncSession) -> None:
    """The JSON endpoint returns an explicit empty shape before any rating cycle runs."""
    app = _build_help_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/help/rating/difficulty-distribution")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "counts": [],
        "total_problems": 0,
        "unmeasured_problems": 0,
        "min_attempts": MIN_ATTEMPTS_FOR_DISPLAY,
        "computed_at": None,
    }


@pytest.mark.asyncio
async def test_difficulty_distribution_returns_latest_snapshot(session: AsyncSession) -> None:
    """The JSON endpoint surfaces the persisted histogram snapshot once one exists."""
    await persist_difficulty_histogram(session, [1, 55, 100], datetime.now(UTC), unmeasured_problems=4)
    await session.commit()

    app = _build_help_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/help/rating/difficulty-distribution")

    assert response.status_code == 200
    payload = response.json()
    assert payload["bins"] == BIN_COUNT
    assert payload["total_problems"] == 3
    assert sum(payload["counts"]) == 3
    assert payload["unmeasured_problems"] == 4
    assert payload["min_attempts"] == MIN_ATTEMPTS_FOR_DISPLAY
    assert payload["computed_at"] is not None
