#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
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
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import event, text
from sqlalchemy.orm.attributes import get_history

import arena.models.arena_problems  # noqa: F401 – register mapper before event.listen
from arena.models.arena_problems import ArenaProblem
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
