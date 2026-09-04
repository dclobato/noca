#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Round-trip tests for the arena_users username migration.

These run the migration's real ``upgrade()`` and ``downgrade()`` bodies against
a temporary SQLite database, following
``tests/web/test_contest_global_medals_migration.py``. Asserting the SQL
constants alone would not catch an ALTER that SQLite rejects, which is the whole
reason the migration uses batch mode.

The engine must be **file-backed**: batch mode recreates the table, which an
in-memory database does not survive across connections.
"""

from __future__ import annotations

import importlib.util
import re
import tempfile
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, create_engine, exc, inspect

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "migrations" / "versions" / "202608310001_add_arena_user_username.py"
)

_NEW_COLUMNS = ("username", "full_name_public", "dta_troca_username", "consent_generation")

_GENERATED_USERNAME = re.compile(r"^[a-z]+-[a-z]+-[0-9]{3}$")
_FALLBACK_USERNAME = re.compile(r"^user-[0-9a-f]{12}$")

# Real UUIDs, because the fallback handle is derived from the id by stripping
# hyphens and taking twelve characters: short synthetic ids would not exercise
# that expression.
_ADULT = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
_MINOR = "1a2b3c4d-5e6f-7081-9203-b4c5d6e7f809"
_NO_DOB = "2b3c4d5e-6f70-8192-a3b4-c5d6e7f80912"
_VIOLATING = "3c4d5e6f-7081-92a3-b4c5-d6e7f8091223"

# Pre-migration shape of `arena_users`, reduced to the columns this migration
# reads or writes. `public_profile`, `ranking_visible` and `dta_nascimento` are
# all required: the CHECK constraint and the minor shield reference them, and
# batch-mode table recreation would fail for an unrelated reason without them.
_PRE_MIGRATION_DDL = (
    """
    CREATE TABLE arena_users (
        id VARCHAR(36) PRIMARY KEY,
        nome VARCHAR(120) NOT NULL,
        dta_nascimento DATE,
        public_profile BOOLEAN NOT NULL DEFAULT 0,
        ranking_visible BOOLEAN NOT NULL DEFAULT 1
    )
    """,
)


def _load_migration() -> ModuleType:
    """Import the migration module by file path (its name starts with a digit)."""
    spec = importlib.util.spec_from_file_location("arena_username_migration_under_test", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_module(module: ModuleType, engine: Engine, direction: str) -> None:
    """Run an already-loaded migration module's upgrade() or downgrade().

    Kept separate from :func:`_run` so a test can patch the module's word-list
    paths first. Reloading the module, as ``_run`` does, would discard the
    patch and silently exercise the normal path instead.
    """
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            getattr(module, direction)()


def _run(engine: Engine, direction: str) -> None:
    """Run the migration's upgrade() or downgrade() against a live connection."""
    _run_module(_load_migration(), engine, direction)


def _column_names(engine: Engine, table: str) -> set[str]:
    with engine.connect() as connection:
        return {column["name"] for column in inspect(connection).get_columns(table)}


def _usernames(engine: Engine) -> list[str]:
    with engine.connect() as connection:
        return [row[0] for row in connection.exec_driver_sql("SELECT username FROM arena_users").fetchall()]


def _row(engine: Engine, user_id: str) -> tuple[int, int, int]:
    with engine.connect() as connection:
        return connection.exec_driver_sql(  # type: ignore[return-value]
            "SELECT public_profile, ranking_visible, full_name_public FROM arena_users WHERE id = ?",
            (user_id,),
        ).one()


@pytest.fixture
def pre_migration_engine() -> Iterator[Engine]:
    """A file-backed SQLite database in the pre-migration shape with seeded rows."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as handle:
        path = Path(handle.name)
    engine = create_engine(f"sqlite:///{path}")
    today = datetime.now(UTC).date()
    adult = today.replace(year=today.year - 30).isoformat()
    minor = today.replace(year=today.year - 17).isoformat()
    with engine.begin() as connection:
        for statement in _PRE_MIGRATION_DDL:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql(
            "INSERT INTO arena_users (id, nome, dta_nascimento, public_profile, ranking_visible) VALUES"
            " (?, 'Adult User', ?, 1, 1),"
            " (?, 'Minor User', ?, 1, 1),"
            " (?, 'Unknown Age', NULL, 1, 1),"
            " (?, 'Invariant Breaker', ?, 1, 0)",
            (_ADULT, adult, _MINOR, minor, _NO_DOB, _VIOLATING, adult),
        )
    try:
        yield engine
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)


def test_upgrade_adds_the_four_columns(pre_migration_engine: Engine) -> None:
    """The upgrade adds exactly the four identity columns."""
    before = _column_names(pre_migration_engine, "arena_users")
    _run(pre_migration_engine, "upgrade")

    assert _column_names(pre_migration_engine, "arena_users") == before | set(_NEW_COLUMNS)


def test_upgrade_backfills_every_row_with_a_unique_handle(pre_migration_engine: Engine) -> None:
    """No row is left without a username, and no two rows share one."""
    _run(pre_migration_engine, "upgrade")

    usernames = _usernames(pre_migration_engine)
    assert len(usernames) == 4
    assert all(name for name in usernames)
    assert len(set(usernames)) == 4
    assert all(_GENERATED_USERNAME.fullmatch(name) for name in usernames), usernames


def test_upgrade_makes_username_not_null_and_unique(pre_migration_engine: Engine) -> None:
    """Both halves of the uniqueness guarantee are installed."""
    _run(pre_migration_engine, "upgrade")

    with pre_migration_engine.begin() as connection:
        taken = connection.exec_driver_sql("SELECT username FROM arena_users WHERE id = ?", (_ADULT,)).scalar_one()

    with pytest.raises(exc.IntegrityError), pre_migration_engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO arena_users (id, nome, username, public_profile, ranking_visible)"
            " VALUES ('11111111-1111-1111-1111-111111111111', 'Dupe', ?, 0, 1)",
            (taken,),
        )

    with pytest.raises(exc.IntegrityError), pre_migration_engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO arena_users (id, nome, username, public_profile, ranking_visible)"
            " VALUES ('22222222-2222-2222-2222-222222222222', 'Nameless', NULL, 0, 1)"
        )


def test_upgrade_installs_the_public_profile_check(pre_migration_engine: Engine) -> None:
    """A public profile without ranking visibility is rejected by the database."""
    _run(pre_migration_engine, "upgrade")

    with pytest.raises(exc.IntegrityError), pre_migration_engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO arena_users (id, nome, username, public_profile, ranking_visible)"
            " VALUES ('33333333-3333-3333-3333-333333333333', 'Bad', 'bad-handle-001', 1, 0)"
        )


def test_upgrade_fixes_the_pre_existing_invariant_violation(pre_migration_engine: Engine) -> None:
    """A row that already violated the invariant is repaired, not rejected."""
    _run(pre_migration_engine, "upgrade")

    public_profile, ranking_visible, _ = _row(pre_migration_engine, _VIOLATING)
    assert public_profile == 0
    assert ranking_visible == 0


@pytest.mark.parametrize("user_id", [_MINOR, _NO_DOB])
def test_upgrade_shields_minors_without_touching_ranking_visibility(pre_migration_engine: Engine, user_id: str) -> None:
    """Shielding clears the two publication flags and nothing else.

    `ranking_visible` staying set is the point: an age-shielded user remains in
    the public ranking under their pseudonym. An unknown date of birth is
    treated as a minor, so the shield fails closed.
    """
    _run(pre_migration_engine, "upgrade")

    public_profile, ranking_visible, full_name_public = _row(pre_migration_engine, user_id)
    assert public_profile == 0
    assert full_name_public == 0
    assert ranking_visible == 1


def test_upgrade_leaves_an_adult_published(pre_migration_engine: Engine) -> None:
    """An adult's existing public profile survives the shield."""
    _run(pre_migration_engine, "upgrade")

    public_profile, ranking_visible, _ = _row(pre_migration_engine, _ADULT)
    assert public_profile == 1
    assert ranking_visible == 1


def test_upgrade_keeps_existing_adults_published_under_their_real_name(pre_migration_engine: Engine) -> None:
    """Introducing the handle must not retract a name already being published.

    ``full_name_public`` defaults to false, which is right for an account created
    after this migration. Letting that default reach accounts that already exist
    would pseudonymize every adult on the platform -- across every ranking, public
    profile and solver credit -- without one of them asking for it. The pseudonym
    is here to protect minors, not to change how adults already appear.
    """
    _run(pre_migration_engine, "upgrade")

    _, _, full_name_public = _row(pre_migration_engine, _ADULT)
    assert full_name_public == 1


@pytest.mark.parametrize("user_id", [_MINOR, _NO_DOB])
def test_upgrade_never_publishes_a_shielded_name(pre_migration_engine: Engine, user_id: str) -> None:
    """The adult backfill must not reach a minor or an unknown date of birth.

    The mirror of the test above, and the reason the shield runs last: the two
    statements are complementary, so an ordering mistake would show up here as a
    published legal name rather than as a passing suite.
    """
    _run(pre_migration_engine, "upgrade")

    _, _, full_name_public = _row(pre_migration_engine, user_id)
    assert full_name_public == 0


def test_backfill_folds_accented_title_case_word_lists(pre_migration_engine: Engine, tmp_path: Path) -> None:
    """Backfilled handles are folded to lowercase ASCII.

    The shipped lists are Title-Case with diacritics, but only a minority of
    entries are accented, so drawing from them would let a broken fold pass by
    chance. Both lists are replaced with a single accented entry so the expected
    output is exact.
    """
    animals = tmp_path / "animais.txt"
    adjectives = tmp_path / "adjetivos.txt"
    animals.write_text("Camaleão\n", encoding="utf-8")
    adjectives.write_text("Ágil\n", encoding="utf-8")

    module = _load_migration()
    module._ANIMALS_PATH = animals
    module._ADJECTIVES_PATH = adjectives
    _run_module(module, pre_migration_engine, "upgrade")

    usernames = _usernames(pre_migration_engine)
    assert all(re.fullmatch(r"^camaleao-agil-[0-9]{3}$", name) for name in usernames), usernames


def test_backfill_falls_back_when_word_lists_are_missing(pre_migration_engine: Engine, tmp_path: Path) -> None:
    """An unreadable word list yields id-derived handles instead of failing."""
    module = _load_migration()
    module._ANIMALS_PATH = tmp_path / "absent-animais.txt"
    module._ADJECTIVES_PATH = tmp_path / "absent-adjetivos.txt"
    _run_module(module, pre_migration_engine, "upgrade")

    usernames = _usernames(pre_migration_engine)
    assert len(usernames) == 4
    assert all(_FALLBACK_USERNAME.fullmatch(name) for name in usernames), usernames


def test_downgrade_removes_the_columns_and_keeps_the_rows(pre_migration_engine: Engine) -> None:
    """Downgrading restores the original column set without losing data."""
    before = _column_names(pre_migration_engine, "arena_users")
    _run(pre_migration_engine, "upgrade")
    _run(pre_migration_engine, "downgrade")

    assert _column_names(pre_migration_engine, "arena_users") == before
    with pre_migration_engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM arena_users").scalar_one() == 4


def test_downgrade_drops_the_public_profile_check(pre_migration_engine: Engine) -> None:
    """The CHECK is gone after a downgrade, so the old shape inserts again."""
    _run(pre_migration_engine, "upgrade")
    _run(pre_migration_engine, "downgrade")

    with pre_migration_engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO arena_users (id, nome, dta_nascimento, public_profile, ranking_visible)"
            " VALUES ('44444444-4444-4444-4444-444444444444', 'After', ?, 1, 0)",
            (date(2000, 1, 1).isoformat(),),
        )
