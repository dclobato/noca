#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# Environment variables MUST be set before any web.* import because
# web.config.Settings() is instantiated at module load time and requires these.
import asyncio
import os
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

_xdist_worker = os.environ.get("PYTEST_XDIST_WORKER", "")
_storage_label = _xdist_worker or "serial"
_test_run_id = os.environ.setdefault("NOCA_TEST_RUN_ID", str(os.getpid()))
_tmp_stmt = tempfile.mkdtemp(prefix=f"noca-test-statements-{_storage_label}-")
_tmp_tc = tempfile.mkdtemp(prefix=f"noca-testcases-{_storage_label}-")

os.environ.setdefault("NOCA_DB_USER", "test")
os.environ.setdefault("NOCA_DB_PASSWORD", "test")
os.environ.setdefault("NOCA_DB_SERVER", "localhost")
os.environ.setdefault("NOCA_DB_NAME", "test")
os.environ.setdefault("NOCA_JWT_SECRET_KEY", "0a2b72ba8dc0cf8798d19b8e9fd5ae5361d294588cc8c5056abc5af1eb4b17a6d")
os.environ.setdefault("NOCA_WEB_ENABLE_CLARIFICATION_REAPER", "false")

# Settings that reach rendered HTML are pinned to their documented defaults so a
# developer's `.env` cannot change what a render assertion sees. `arena/config.py`
# and `web/config.py` both declare `env_file=".env"`, and pydantic-settings ranks
# environment variables above that file, so these win. Without them a local
# `NOCA_IMAGE_MAX_FILE_SIZE=5242880` silently rewrites every upload-limit
# assertion, and a local `NOCA_ARENA_BRAND_NAME` every footer one.
os.environ.setdefault("NOCA_ARENA_BRAND_NAME", "NOCA Arena")
os.environ.setdefault("NOCA_WEB_BRAND_NAME", "NOCA Contest")
os.environ.setdefault("NOCA_IMAGE_MAX_FILE_SIZE", str(2 * 1024 * 1024))
os.environ.setdefault("NOCA_IMAGE_MAX_WIDTH", "2048")
os.environ.setdefault("NOCA_IMAGE_MAX_HEIGHT", "2048")
os.environ.setdefault("NOCA_JWT_REFRESH_MAX_SESSION_SECONDS", "0")
os.environ.setdefault("NOCA_ARENA_PRESENCE_ENABLED", "true")
os.environ.setdefault("NOCA_ARENA_PRESENCE_HEARTBEAT_SECONDS", "30")
if _xdist_worker:
    # The controller's environment is inherited by every xdist worker. Override
    # shared application paths inside each worker so parallel tests cannot see or
    # reconcile another worker's in-flight filesystem artifacts.
    os.environ["NOCA_WEB_PROBLEM_STATEMENT_DIR"] = _tmp_stmt
    os.environ["NOCA_PROBLEM_TESTCASE_DIR"] = _tmp_tc
else:
    os.environ.setdefault("NOCA_WEB_PROBLEM_STATEMENT_DIR", _tmp_stmt)
    os.environ.setdefault("NOCA_PROBLEM_TESTCASE_DIR", _tmp_tc)
os.environ.setdefault("NOCA_VALKEY_SERVER", "127.0.0.1")
os.environ.setdefault("NOCA_VALKEY_PORT", "6379")
# Each pytest-xdist worker gets its own Valkey logical DB so per-test
# flushdb calls never wipe keys written by tests running on another worker.
# Plain (serial) runs keep DB 15; worker gwN uses 15 - ((N + 1) % 15)
# (gw0 -> 14, gw1 -> 13, ..., gw13 -> 1). DB 0 stays untouched because it is
# the apps' configured default. This supports up to 14 parallel workers.
_test_valkey_db = 15 - (int(_xdist_worker[2:]) + 1) % 15 if _xdist_worker.startswith("gw") else 15
os.environ["NOCA_VALKEY_DB"] = str(_test_valkey_db)
# Valkey Pub/Sub ignores logical databases, so DB 15 alone cannot prevent a
# local application on DB 0 from receiving test messages. Give serial and each
# xdist worker a distinct channel namespace before shared constants are imported.
os.environ["NOCA_TEST_VALKEY_CHANNEL_NAMESPACE"] = f"noca:test:{_test_run_id}:{_storage_label}"

import aiosqlite.core  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
import valkey.asyncio as aivalkey  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from valkey.exceptions import ResponseError  # noqa: E402

sqlite3.register_adapter(datetime, lambda d: d.isoformat())
sqlite3.register_converter("DATETIME", lambda s: datetime.fromisoformat(s.decode()))

_REAL_DB_FIXTURES = {"engine", "session"}
_REAL_VALKEY_FIXTURES = {"valkey_client", "sync_valkey_client"}


def pytest_sessionstart(session: pytest.Session) -> None:
    """Flush the test Valkey database before any test starts.

    This makes a new pytest invocation resilient to a previously interrupted run
    that left queue or lock keys behind. Per-test fixtures still flush before and
    after each Valkey integration test for isolation inside the current run.
    """
    import valkey as sync_valkey

    client = sync_valkey.Valkey.from_url(
        settings.valkey_url,
        decode_responses=True,
        socket_connect_timeout=0.5,
        socket_timeout=5.0,
    )
    try:
        client.ping()
        client.flushdb()
    except Exception:
        return
    finally:
        client.close()  # type: ignore[no-untyped-call]


async def _patched_aiosqlite_connect(self: aiosqlite.core.Connection) -> aiosqlite.core.Connection:
    if self._connection is None:
        self._connection = self._connector()
    return self


async def _patched_aiosqlite_execute(self: aiosqlite.core.Connection, fn, *args, **kwargs):
    if not self._running or not self._connection:
        raise ValueError("Connection closed")
    function = partial(fn, *args, **kwargs)
    return function()


def _patched_aiosqlite_await(self: aiosqlite.core.Connection):
    return _patched_aiosqlite_connect(self).__await__()


async def _patched_aiosqlite_close(self: aiosqlite.core.Connection) -> None:
    if self._connection is None:
        return
    try:
        self._conn.close()
    finally:
        self._connection = None
        self._running = False


# Python 3.14 in this environment hangs when sqlite3.connect() is called from a
# worker thread. aiosqlite normally uses a background thread for all DBAPI
# calls, so patch it into a same-thread mode for tests.
aiosqlite.core.Connection._connect = _patched_aiosqlite_connect
aiosqlite.core.Connection._execute = _patched_aiosqlite_execute
aiosqlite.core.Connection.__await__ = _patched_aiosqlite_await
aiosqlite.core.Connection.close = _patched_aiosqlite_close

# Import all models to ensure SQLAlchemy mapper registry is populated
# before Base.metadata.create_all is called.
import web.models  # noqa: E402, F401
from shared.enumerations import ProblemValidatorType, RoleEnum  # noqa: E402
from web.config import settings  # noqa: E402
from web.database import Base  # noqa: E402

if _test_valkey_db != settings.VALKEY_DB:
    raise RuntimeError(
        f"Tests must use Valkey DB {_test_valkey_db} (worker {_xdist_worker or 'serial'}), got DB {settings.VALKEY_DB}"
    )
from tests.fixtures.interif_2026 import (  # noqa: E402
    InterIF2026ContestFixture,
    load_interif_2026_contest,
)
from web.models.contest import Contest  # noqa: E402
from web.models.problem import Problem, ProblemTestCase  # noqa: E402
from web.models.users import UberAdmin, User  # noqa: E402

# ---------------------------------------------------------------------------
# Database fixtures — one fresh file-backed SQLite DB per test function.
# Using function scope avoids event-loop / session-scope isolation issues
# with pytest-asyncio, and SQLite schema creation is effectively free.
# ---------------------------------------------------------------------------


def _path_is_writable_dir(path: Path) -> bool:
    """Return whether path is an existing directory writable by this process."""
    if not path.is_dir():
        return False

    probe = path / f".noca-pytest-{os.getpid()}.tmp"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError:
        return False
    return True


def _sqlite_temp_dir(
    *,
    memory_candidates: Iterable[Path] = (Path("/dev/shm"),),
    fallback_dir: Path | None = None,
) -> Path:
    """Return the fastest safe directory for throwaway SQLite test databases."""
    for candidate in memory_candidates:
        if _path_is_writable_dir(candidate):
            return candidate
    return fallback_dir or Path(tempfile.gettempdir())


def _configure_sqlite_test_connection(dbapi_connection: Any, _connection_record: Any) -> None:
    """Disable durable SQLite journal semantics for per-test throwaway databases."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=MEMORY")
        cursor.execute("PRAGMA synchronous=OFF")
    finally:
        cursor.close()


@pytest_asyncio.fixture
async def engine():
    """Fresh file-backed SQLite engine with schema for each test."""
    fd, path_str = tempfile.mkstemp(suffix=".sqlite3", dir=_sqlite_temp_dir())
    os.close(fd)
    path = Path(path_str)
    e = create_async_engine(
        f"sqlite+aiosqlite:///{path}",
        connect_args={"check_same_thread": False},
    )
    event.listen(e.sync_engine, "connect", _configure_sqlite_test_connection)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield e
    await e.dispose()
    path.unlink(missing_ok=True)


@pytest_asyncio.fixture
async def session(engine) -> AsyncSession:
    """Per-test async session; rolls back on teardown."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()


@pytest_asyncio.fixture
async def interif_2026_contest_fixture(
    session: AsyncSession,
    uberadmin: UberAdmin,
) -> InterIF2026ContestFixture:
    """Populate the full IX InterIF 2026 local contest dataset."""
    return await load_interif_2026_contest(session, uberadmin)


# ---------------------------------------------------------------------------
# Entity fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def uberadmin(session: AsyncSession) -> UberAdmin:
    ua = UberAdmin(
        username="uberadmin_test",
        fullname="UberAdmin Test",
        email_normalizado="ua@test.example.com",
    )
    ua.password = "TestPass1!"
    session.add(ua)
    await session.flush()
    return ua


@pytest_asyncio.fixture
async def running_contest(session: AsyncSession, uberadmin: UberAdmin) -> Contest:
    """A contest that is currently running (started 30 min ago, lasts 2 h)."""
    contest = Contest(
        contest_name="Test Contest",
        contest_url="http://test.example.com",
        login_slug="test-contest",
        start_time=datetime.now(UTC) - timedelta(minutes=30),
        duration_minutes=120,
        stop_answers_after=120,
        stop_updating_scoreboard=120,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    session.add(contest)
    await session.flush()
    return contest


@pytest_asyncio.fixture
async def stopped_contest(session: AsyncSession, uberadmin: UberAdmin) -> Contest:
    """A contest that has already ended (started 5 h ago, lasted 1 h)."""
    contest = Contest(
        contest_name="Stopped Contest",
        contest_url="http://stopped.example.com",
        login_slug="stopped-contest",
        start_time=datetime.now(UTC) - timedelta(hours=5),
        duration_minutes=60,
        stop_answers_after=60,
        stop_updating_scoreboard=60,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    session.add(contest)
    await session.flush()
    return contest


def _make_user(
    session: AsyncSession,
    contest: Contest,
    uberadmin: UberAdmin,
    username: str,
    fullname: str,
    role: RoleEnum,
) -> User:
    user = User(
        username=username,
        fullname=fullname,
        role=role,
        contest_id=contest.id,
        created_by_uberadmin_id=uberadmin.id,
    )
    user.password = "TestPass1!"
    session.add(user)
    return user


@pytest_asyncio.fixture
async def team_user(session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin) -> User:
    user = _make_user(session, running_contest, uberadmin, "team_a", "Team A", RoleEnum.TEAM)
    await session.flush()
    return user


@pytest_asyncio.fixture
async def another_team_user(session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin) -> User:
    user = _make_user(session, running_contest, uberadmin, "team_b", "Team B", RoleEnum.TEAM)
    await session.flush()
    return user


@pytest_asyncio.fixture
async def judge_user(session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin) -> User:
    user = _make_user(session, running_contest, uberadmin, "judge_x", "Judge X", RoleEnum.JUDGE)
    await session.flush()
    return user


@pytest_asyncio.fixture
async def another_judge_user(session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin) -> User:
    user = _make_user(session, running_contest, uberadmin, "judge_y", "Judge Y", RoleEnum.JUDGE)
    await session.flush()
    return user


@pytest_asyncio.fixture
async def admin_user(session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin) -> User:
    user = _make_user(session, running_contest, uberadmin, "admin_1", "Admin 1", RoleEnum.ADMIN)
    await session.flush()
    return user


@pytest_asyncio.fixture
async def contest_problem(session: AsyncSession, running_contest: Contest) -> Problem:
    problem = Problem(
        contest_id=running_contest.id,
        title="Test Problem A",
        ordinal=1,
        color="#ff0000",
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(problem)
    await session.flush()
    return problem


@pytest_asyncio.fixture
async def judgeable_contest_problem(session: AsyncSession, contest_problem: Problem) -> Problem:
    """A contest problem carrying the one test case a submission now requires."""
    session.add(
        ProblemTestCase(
            problem_id=contest_problem.id,
            ordinal=1,
            is_sample=True,
            input_size_bytes=2,
            output_size_bytes=2,
        )
    )
    await session.flush()
    return contest_problem


@pytest_asyncio.fixture
async def valkey_client():
    """
    Real Valkey client against the isolated logical DB for this test worker.

    The database is flushed before and after each test so queue assertions stay isolated.
    """
    pool = aivalkey.ConnectionPool.from_url(
        settings.valkey_url,
        decode_responses=True,
        socket_connect_timeout=0.5,
        socket_timeout=5.0,
    )
    client = aivalkey.Valkey.from_pool(pool)
    try:
        await asyncio.wait_for(client.ping(), timeout=1.0)
        await asyncio.wait_for(client.flushdb(), timeout=1.0)
    except (ResponseError, TimeoutError, OSError, Exception) as exc:
        await client.aclose()
        await pool.aclose()
        pytest.skip(f"Valkey at {settings.valkey_url} is unavailable for tests: {exc}")
    try:
        yield client
    finally:
        await asyncio.wait_for(client.flushdb(), timeout=1.0)
        await client.aclose()
        await pool.aclose()


@pytest.fixture
def sync_valkey_client():
    """
    Synchronous Valkey client against the isolated logical DB for this test worker.

    Flushes the database before and after each test for isolation.
    """
    import valkey as sync_valkey
    from valkey.exceptions import ConnectionError as ValkeyConnError

    client = sync_valkey.Valkey.from_url(
        settings.valkey_url,
        decode_responses=True,
        socket_connect_timeout=0.5,
        socket_timeout=5.0,
    )
    try:
        client.ping()
        client.flushdb()
    except (ValkeyConnError, TimeoutError, OSError, Exception) as exc:
        client.close()  # type: ignore[no-untyped-call]
        pytest.skip(f"Valkey at {settings.valkey_url} is unavailable for tests: {exc}")
    try:
        yield client
    finally:
        try:
            client.flushdb()
        finally:
            client.close()  # type: ignore[no-untyped-call]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-tag tests that exercise real infrastructure fixtures."""
    for item in items:
        fixture_names = set(item.fixturenames)
        if fixture_names & _REAL_DB_FIXTURES:
            item.add_marker(pytest.mark.real_db)
        if fixture_names & _REAL_VALKEY_FIXTURES:
            item.add_marker(pytest.mark.real_valkey)


# ── Full-suite audit: every skip must be one we sanctioned ───────────────────
#
# A skip is indistinguishable from a pass in a summary line. That is not
# hypothetical here: the animator remote's Kotlin contract test spent a release
# cycle skipping on a stale image pin while CI stayed green and said nothing.
#
# Four groups of tests legitimately cannot run in CI, every one because it needs
# a credential or a live service deliberately not wired into it:
#
#   real_docker           the built judge images and a Docker daemon
#   real_openai           a paid OpenAI key
#   real_ipqualityscore   a paid IPQualityScore key
#   tests/browser/        Playwright driving a *running* Web/Arena instance
#
# Everything else must run. Under CI -- or wherever NOCA_REQUIRE_FULL_SUITE is
# set -- any other skip fails the session and names itself, so a test cannot
# quietly stop running because a service, a toolchain, or a fixture went away.
#
# The browser suite is matched by path rather than by a marker because its skips
# include a *collection*-level `importorskip`, which produces a report with no
# markers on it at all.
_SANCTIONED_SKIP_MARKERS = frozenset({"real_docker", "real_openai", "real_ipqualityscore"})
_SANCTIONED_SKIP_PREFIXES = ("tests/browser/",)
_REQUIREMENT_DISABLED_VALUES = frozenset({"", "0", "false", "no"})

_unsanctioned_skips: dict[str, str] = {}
_sanctioned_skips: dict[str, str] = {}


def full_suite_is_required(environ: Mapping[str, str]) -> bool:
    """Return whether an unsanctioned skip must fail the session.

    `CI` is the default signal. `NOCA_REQUIRE_FULL_SUITE` states the demand
    explicitly and wins in both directions whenever it is present at all,
    including when empty: an environment that reports `CI` but genuinely cannot
    run part of the suite needs a deliberate way out, and a developer machine
    may want the guarantee without pretending to be CI.
    """
    override = environ.get("NOCA_REQUIRE_FULL_SUITE")
    if override is not None:
        return override.strip().lower() not in _REQUIREMENT_DISABLED_VALUES
    return bool(environ.get("CI"))


def skip_is_sanctioned(nodeid: str, markers: Iterable[str]) -> bool:
    """Return whether this skip is one of the four sanctioned groups."""
    if any(nodeid.startswith(prefix) for prefix in _SANCTIONED_SKIP_PREFIXES):
        return True
    return bool(_SANCTIONED_SKIP_MARKERS.intersection(markers))


def _skip_reason(report: Any) -> str:
    """Return a report's skip reason, without pytest's `Skipped: ` prefix."""
    longrepr = getattr(report, "longrepr", None)
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        return str(longrepr[2]).removeprefix("Skipped: ").strip()
    return str(longrepr).strip() if longrepr else "no reason given"


def _record_skip(nodeid: str, markers: Iterable[str], reason: str) -> None:
    bucket = _sanctioned_skips if skip_is_sanctioned(nodeid, markers) else _unsanctioned_skips
    bucket.setdefault(nodeid, reason)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Record every skipped test, sanctioned or not."""
    # An xfail also reports as skipped; it is a recorded expectation, not a
    # test that failed to run, so it is not this audit's business.
    if not report.skipped or getattr(report, "wasxfail", None) is not None:
        return
    _record_skip(report.nodeid, report.keywords, _skip_reason(report))


def pytest_collectreport(report: pytest.CollectReport) -> None:
    """Record modules skipped during collection (e.g. a missing `importorskip`)."""
    if report.skipped:
        _record_skip(report.nodeid, (), _skip_reason(report))


def pytest_terminal_summary(terminalreporter: Any) -> None:
    """State what was skipped and why, so a summary line cannot hide it."""
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return
    if not _sanctioned_skips and not _unsanctioned_skips:
        return

    terminalreporter.write_sep("=", "skip audit")
    for reason, count in sorted(Counter(_sanctioned_skips.values()).items()):
        terminalreporter.write_line(f"  sanctioned  {count:>4} x {reason}")
    for nodeid, reason in sorted(_unsanctioned_skips.items()):
        terminalreporter.write_line(f"  UNSANCTIONED  {nodeid}: {reason}")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail the session when a test skipped that this environment requires."""
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return
    if not _unsanctioned_skips or not full_suite_is_required(os.environ):
        return

    session.exitstatus = pytest.ExitCode.TESTS_FAILED
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:  # pragma: no cover - terminal plugin is always present
        return
    reporter.write_sep("=", "FULL SUITE REQUIRED", red=True, bold=True)
    reporter.write_line(
        f"{len(_unsanctioned_skips)} test(s) skipped that this environment requires to run. "
        "Fix the cause, or -- if the skip is legitimate here -- add its marker to "
        "_SANCTIONED_SKIP_MARKERS in tests/conftest.py. "
        "Set NOCA_REQUIRE_FULL_SUITE to an empty value to allow skipping."
    )
