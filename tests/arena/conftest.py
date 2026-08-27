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
from arena.models.arena_problems import ArenaProblem
from arena.routes.presence import router as arena_presence_router
from arena.template_globals import register_arena_template_globals
from shared.services.email_reputation import EmailReputationService
from shared.services.network_utils import NetworkService
from shared.services.network_utils.ip_reputation import IPQualityScoreIPReputationService


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
    app.include_router(arena_presence_router)


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
