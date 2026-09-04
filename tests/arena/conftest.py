#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena-specific pytest fixtures.

Provides a SQLite-compatible ``arena_number`` sequence simulator.  In
production, ``arena_number`` is assigned by the PostgreSQL sequence
``arena_problem_arena_number_seq`` (set up by migration
``202605230003_add_arena_number_to_arena_problems.py``).  SQLite test
databases do not run migrations and therefore have no sequence; the
``_sqlite_arena_number`` fixture installs a synchronous ``before_insert``
mapper event that computes ``MAX(arena_number) + 1`` within the current
transaction, matching PostgreSQL's sequence semantics for single-session
tests.
"""

import logging
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader
from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm.attributes import get_history

import arena.models.arena_problems  # noqa: F401 – register mapper before event.listen
from arena.config import settings
from arena.dependencies.problem_export_rate_limit import PROBLEM_EXPORT_LIMITER
from arena.dependencies.user_read_rate_limit import USER_READ_LIMITER
from arena.models.arena_problems import ArenaProblem
from arena.routes.admin_announcements import router as arena_admin_announcements_router
from arena.routes.admin_dashboard_lockouts import router as arena_admin_dashboard_lockouts_router
from arena.routes.announcements import router as arena_announcements_router
from arena.routes.auth_common import AUTH_RATE_LIMITER, SIGNUP_RATE_LIMITER, SIGNUP_REQUEST_RATE_LIMITER
from arena.routes.presence import router as arena_presence_router
from arena.services.ai_review_request_service import AI_REVIEW_RATE_LIMITER
from arena.services.geocode_service import GEOCODE_USER_RATE_LIMITER
from arena.services.required_announcement_cache import invalidate_required_announcements_cache
from arena.template_globals import register_arena_template_globals
from shared.services.email_reputation import EmailReputationService
from shared.services.network_utils import NetworkService
from shared.services.network_utils.ip_reputation import IPQualityScoreIPReputationService
from shared.services.rejudge_cooldown import reset_local_windows


def attach_reputation_services(app: FastAPI) -> None:
    """Attach disabled IPQualityScore reputation services to a test app.

    The signup route builds a post-signup background task that reads these from
    ``app.state``; wiring disabled services (no API key) keeps the task inert.
    """
    logger = logging.getLogger(__name__)
    app.state.ip_reputation_service = IPQualityScoreIPReputationService(
        api_key=None, network_service=NetworkService(logger=logger), logger=logger
    )
    app.state.email_reputation_service = EmailReputationService(
        api_key=None, network_service=NetworkService(logger=logger), logger=logger
    )


_ARENA_DIR = Path(__file__).resolve().parents[2] / "arena"
_SHARED_DIR = Path(__file__).resolve().parents[2] / "shared"


def install_arena_templates(app: FastAPI, *, app_version: str = "test") -> Jinja2Templates:
    """Build the Arena template environment exactly as production does.

    Test applications used to hand-assemble ``env.globals``, each declaring
    whichever subset its page happened to touch. Those subsets drifted, and the
    templates hid the gaps behind ``is defined`` guards. Sharing one registrar
    with `arena/main.py` means a template global cannot be present in production
    and missing under test.

    Args:
        app: Application whose ``state.arena_templates`` should be populated.
        app_version: Version string rendered in the footer and asset cache keys.

    Returns:
        The configured templates object, for tests that override a global.
    """
    templates = Jinja2Templates(directory=_ARENA_DIR / "template")
    templates.env.loader = ChoiceLoader(
        [
            FileSystemLoader(str(_ARENA_DIR / "template")),
            FileSystemLoader(str(_SHARED_DIR / "template")),
        ]
    )
    register_arena_template_globals(templates, app_version=app_version)
    app.state.arena_templates = templates
    return templates


def mount_arena_base_routes(app: FastAPI) -> None:
    """Mount the static routes and presence router that ``_base.html`` always resolves.

    Feature routers stay the caller's business; this is only the floor every
    page extending ``_base.html`` needs in order to render at all.

    Args:
        app: Application to mount the base routes on.
    """
    app.mount("/static/css", StaticFiles(directory=_ARENA_DIR / "static" / "css"), name="arena_static_css")
    app.mount("/static/js", StaticFiles(directory=_ARENA_DIR / "static" / "js"), name="arena_static_js")
    app.mount("/static/img", StaticFiles(directory=_ARENA_DIR / "static" / "img"), name="arena_static_img")
    app.mount("/static/vendor", StaticFiles(directory=_SHARED_DIR / "static" / "vendor"), name="static_vendor")
    app.mount("/static/shared-js", StaticFiles(directory=_SHARED_DIR / "static" / "js"), name="static_shared_js")
    app.mount("/static/shared-img", StaticFiles(directory=_SHARED_DIR / "static" / "img"), name="static_shared_img")
    app.include_router(arena_presence_router)
    # The sidebar resolves the public announcement board on every page and its
    # management entry on every admin-dashboard page, so both real routers are part
    # of the floor rather than a stub every test app has to remember.
    app.include_router(arena_announcements_router)
    app.include_router(arena_admin_announcements_router)
    # The admin-dashboard sub-nav also resolves the lockouts page on every page it
    # appears on, so that router is part of the floor for the same reason.
    app.include_router(arena_admin_dashboard_lockouts_router)


@pytest.fixture
def sql_statements(engine: Any) -> Generator[list[str]]:
    """Capture SQL statements executed after a test clears the returned list."""
    statements: list[str] = []

    def _capture(
        _connection: Connection,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    yield statements
    event.remove(engine.sync_engine, "before_cursor_execute", _capture)


@pytest.fixture(autouse=True)
def _google_oauth_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin Google sign-in off for every Arena test.

    ``arena.config.settings`` is a module-level singleton that reads the
    developer's own ``.env``, so without this a machine configured for a live
    Google test would render the profile page's Linked-accounts block in every
    test app -- including the ones that do not register the Google router, whose
    ``url_for("arena_google_link")`` then raises ``NoMatchFound``. Tests must not
    depend on who is sitting at the keyboard.

    ``monkeypatch.setattr`` captures the pre-test value and restores it at
    teardown, so a test that turns the feature back on (``build_google_app``
    does exactly that) is still cleaned up afterwards.
    """
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_ENABLED", False)
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret")


@pytest.fixture(autouse=True)
def _reset_auth_fallback_limiters() -> Generator[None]:
    """Clear the process-local auth and signup fallback limiters around each test.

    The Arena test apps carry no ``valkey_runtime``, so both limiters run on
    their in-memory fallback, whose state would otherwise leak across the tests
    of one xdist worker and make signup and login tests order-dependent.
    """
    AUTH_RATE_LIMITER._buckets.clear()
    AUTH_RATE_LIMITER._sets.clear()
    SIGNUP_RATE_LIMITER._buckets.clear()
    SIGNUP_REQUEST_RATE_LIMITER._buckets.clear()
    USER_READ_LIMITER._buckets.clear()
    PROBLEM_EXPORT_LIMITER._buckets.clear()
    AI_REVIEW_RATE_LIMITER._buckets.clear()
    GEOCODE_USER_RATE_LIMITER._buckets.clear()
    reset_local_windows()
    yield
    AUTH_RATE_LIMITER._buckets.clear()
    AUTH_RATE_LIMITER._sets.clear()
    SIGNUP_RATE_LIMITER._buckets.clear()
    SIGNUP_REQUEST_RATE_LIMITER._buckets.clear()
    USER_READ_LIMITER._buckets.clear()
    PROBLEM_EXPORT_LIMITER._buckets.clear()
    AI_REVIEW_RATE_LIMITER._buckets.clear()
    GEOCODE_USER_RATE_LIMITER._buckets.clear()
    reset_local_windows()


@pytest.fixture(autouse=True)
def _reset_required_announcement_cache() -> Generator[None]:
    """Drop the process-local "no required announcement exists" answer around each test.

    The cache is module-global, so a test that loaded a page with nothing
    required would otherwise hide the pop-up from the next test that publishes
    a required announcement straight through the service.
    """
    invalidate_required_announcements_cache()
    yield
    invalidate_required_announcements_cache()


@pytest.fixture(autouse=True)
def _sqlite_arena_number(session: Any) -> Generator[None]:
    """Simulate the PostgreSQL arena_number sequence for SQLite unit tests.

    Registers a synchronous ``before_insert`` mapper event on
    :class:`ArenaProblem` for the duration of each test.  The event is a
    no-op for non-SQLite dialects, so the fixture is safe to leave as
    *autouse* even if the test suite is later run against a real PostgreSQL
    database.

    The event only runs when ``arena_number`` has **not** been explicitly
    provided on the object, matching the behaviour of the PostgreSQL
    sequence (which is skipped when the application explicitly inserts a
    value into that column).
    """

    def _before_insert(mapper: Any, connection: Any, target: Any) -> None:
        if connection.dialect.name != "sqlite":
            return
        # Respect explicitly-provided arena_number values.
        hist = get_history(target, "arena_number")
        if hist.added:
            return
        result = connection.execute(text("SELECT COALESCE(MAX(arena_number), 0) + 1 FROM arena_problems"))
        target.arena_number = result.scalar()

    event.listen(ArenaProblem, "before_insert", _before_insert)
    yield
    event.remove(ArenaProblem, "before_insert", _before_insert)


class FakeGeocodeValkey:
    """In-memory stand-in for the Valkey runtime the geocode service uses.

    Holds the cache in a dict and answers the deployment-wide gate from a scripted
    verdict, so route-level tests can drive admission without a live server. The gate's
    real Lua is exercised against a real Valkey in
    ``tests/arena/test_arena_geocode_throttle.py``.

    The gate is the only script this fake answers. The shared per-key rate limiter runs
    its own single-key script through the same ``eval``; that one is declined (``None``)
    so the limiter uses its process-local fallback, which is real and is reset between
    tests by :func:`_reset_auth_fallback_limiters`.
    """

    #: ``numkeys`` the geocode gate script is invoked with; the limiter's uses one.
    _GATE_NUMKEYS = 2

    def __init__(self, *, gate_reply: object = (1, 0)) -> None:
        """Start with an empty cache and a gate that admits every call.

        Args:
            gate_reply: What the gate script returns; a two-item sequence is a verdict,
                ``None`` simulates Valkey being unable to answer.
        """
        self.store: dict[str, str] = {}
        self.gate_reply = gate_reply
        self.gate_calls = 0

    async def get(self, key: str) -> str | None:
        """Return the cached value for *key*, or None."""
        return self.store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        """Store *value* under *key*; the TTL is accepted and ignored."""
        self.store[key] = value

    async def eval(self, script: str, numkeys: int, *args: str) -> object:
        """Answer the gate script with the scripted verdict; decline anything else."""
        if numkeys != self._GATE_NUMKEYS:
            return None
        self.gate_calls += 1
        return self.gate_reply
