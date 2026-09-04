#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add the Arena username and its companion identity columns.

Adds four columns to ``arena_users``:

- ``username``: the globally unique lowercase handle that becomes every user's
  public display name and deterministic-avatar seed.
- ``full_name_public``: an adult's opt-in to publish their legal name instead.
- ``dta_troca_username``: when the handle was last changed, for the cooldown.
- ``consent_generation``: the parental-consent epoch counter.

It also installs ``ck_arena_users_public_profile_requires_ranking``, which the
database has never enforced despite the column comment claiming it.

Three things about this migration are deliberate and should not be "tidied":

**Existing adults keep their names; only the future is pseudonymous.** The
``full_name_public`` column defaults to false, which is right for an account
created after this lands. Applying that default to accounts that already exist
would silently retract the name every adult is currently listed under, across
every ranking, profile and solver credit, without any of them asking. The
pseudonym exists to protect minors, not to re-identify how adults already
appear -- so ``PRESERVE_ADULT_FULL_NAMES`` sets the flag for every 18+ account
with a recorded date of birth, and nobody has to act to keep what they had.
An adult who would rather be pseudonymous clears the flag themselves.

**The backfill runs here rather than in a follow-up script.** ``username`` is
the public display name the moment this lands. A placeholder cleaned up later
means everyone's public identity is ``user-3f2b1c9d4e6a`` between
``alembic upgrade head`` and an operator remembering to run the script --
permanently, if they never do. The repo's usual migration-plus-script shape is
for backfills that are expensive or fallible; drawing a word pair is neither.
Above roughly 100k Arena users, revert to the two-step shape and accept the
operator step.

**Word-list reading is inlined rather than imported.** A frozen historical
migration must not couple to mutable application code, so this file cannot call
``shared.services.random_username_service``. That means it must repeat that
module's folding: the shipped word lists are Title-Case and carry diacritics
(``Alce``, ``Camaleão``, ``Ágil``), and a reader that skipped the fold would
write handles like ``Camaleão-Ágil-042``, which violate both the lowercase
canonical form and ``arena.services.username_service.USERNAME_PATTERN``.

``downgrade()`` removes the columns and constraints, but the final two
statements are **not reversible**: which accounts had ``public_profile`` set
before the shield ran is not recorded anywhere, and neither is which adults
already had a legal name published. Downgrading and re-upgrading leaves shielded
accounts unpublished -- the safe direction -- and re-derives the adult flag from
age, which restores it for exactly the accounts that would have had it.

Revision ID: 202608310001
Revises: 202608300002
Create Date: 2026-08-31

"""

from __future__ import annotations

import secrets
import unicodedata
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from dateutil.relativedelta import relativedelta

revision: str = "202608310001"
down_revision: str | Sequence[str] | None = "202608300002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Resolved from this file: ``migrations/versions/`` -> repository root ->
#: ``shared/``. Module-level so tests can point them at a missing path and
#: exercise the fallback below.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ANIMALS_PATH = _REPO_ROOT / "shared" / "animais.txt"
_ADJECTIVES_PATH = _REPO_ROOT / "shared" / "adjetivos.txt"

_MAX_DRAWS_PER_ROW = 12

SELECT_ROWS_NEEDING_USERNAME = "SELECT id FROM arena_users WHERE username IS NULL"

BACKFILL_USERNAME = "UPDATE arena_users SET username = :username WHERE id = :id AND username IS NULL"

#: Used when the word lists cannot be read at all -- a slim deploy image that
#: dropped them, say. A derived handle is ugly but unique and, crucially, lets
#: the upgrade complete instead of bricking the deployment.
BACKFILL_USERNAME_FALLBACK = (
    "UPDATE arena_users SET username = 'user-' || substr(replace(id, '-', ''), 1, 12) WHERE username IS NULL"
)

#: Must run before the CHECK is installed: the constraint would otherwise fail
#: to apply against rows that already violate it.
FIX_PUBLIC_PROFILE_INVARIANT = (
    "UPDATE arena_users SET public_profile = false WHERE public_profile AND NOT ranking_visible"
)

#: Existing adults keep publishing the name they were already published under.
#:
#: `full_name_public` defaults to false, which is the right default for an
#: account created *after* this migration: a new user is pseudonymous until they
#: choose otherwise. Applying that default to accounts that already exist would
#: be a different thing entirely -- it would silently retract the name every
#: adult on the platform is currently listed under, on every ranking, profile
#: and solver credit, without any of them asking for it. The pseudonym is
#: introduced here to protect minors, not to re-identify how adults already
#: appear.
#:
#: So the column's default governs the future, and this statement preserves the
#: past. Adults who would rather be pseudonymous can clear the flag themselves;
#: nobody has to act to keep what they had.
PRESERVE_ADULT_FULL_NAMES = (
    "UPDATE arena_users SET full_name_public = true WHERE dta_nascimento IS NOT NULL AND dta_nascimento <= :cutoff"
)

#: A NULL date of birth counts as a minor. The shield fails closed by design:
#: an account whose age is unknown is treated as needing protection.
#: `ranking_visible` is deliberately absent -- an age-shielded user stays in the
#: public ranking, under their pseudonym.
#:
#: Runs *after* PRESERVE_ADULT_FULL_NAMES, against the same cutoff value. The
#: two predicates are complementary, so neither can touch the other's rows --
#: but ordering the shield last means that even if they ever overlapped, the
#: protective statement would be the one that wins.
SHIELD_EXISTING_MINORS = (
    "UPDATE arena_users SET public_profile = false, full_name_public = false "
    "WHERE dta_nascimento IS NULL OR dta_nascimento > :cutoff"
)


def _load_words(path: Path) -> list[str]:
    """Read a word list, folding each entry to lowercase ASCII.

    Mirrors ``shared.services.random_username_service._strip_accents_lower``;
    see this module's docstring for why it is repeated rather than imported.

    Args:
        path: Absolute path to a one-word-per-line list.

    Returns:
        list[str]: The folded, non-empty words in file order.
    """
    words: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        normalized = unicodedata.normalize("NFD", stripped.lower())
        folded = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
        if folded:
            words.append(folded)
    return words


def _fallback_username(user_id: str) -> str:
    """Derive a unique handle from a user id.

    Args:
        user_id: The row's UUID string.

    Returns:
        str: A handle of the form ``user-<12 hex characters>``.
    """
    return f"user-{user_id.replace('-', '')[:12]}"


def _backfill_usernames(connection: sa.Connection) -> None:
    """Give every existing row a handle.

    Falls back to id-derived handles when the word lists are unreadable, so a
    deployment missing them still upgrades.

    Args:
        connection: The live migration connection.
    """
    try:
        animals = _load_words(_ANIMALS_PATH)
        adjectives = _load_words(_ADJECTIVES_PATH)
    except OSError:
        animals, adjectives = [], []
    if not animals or not adjectives:
        connection.execute(sa.text(BACKFILL_USERNAME_FALLBACK))
        return

    used: set[str] = set()
    for row in connection.execute(sa.text(SELECT_ROWS_NEEDING_USERNAME)).fetchall():
        user_id = str(row[0])
        candidate = _fallback_username(user_id)
        for _ in range(_MAX_DRAWS_PER_ROW):
            drawn = f"{secrets.choice(animals)}-{secrets.choice(adjectives)}-{secrets.randbelow(1000):03d}"
            if drawn not in used:
                candidate = drawn
                break
        used.add(candidate)
        connection.execute(sa.text(BACKFILL_USERNAME), {"username": candidate, "id": user_id})


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "arena_users",
        sa.Column(
            "username",
            sa.String(64),
            nullable=True,
            comment="Globally unique lowercase Arena handle; the public display name "
            "for every user and the deterministic-avatar seed.",
        ),
    )
    op.add_column(
        "arena_users",
        sa.Column(
            "full_name_public",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Adult opt-in to show the full name instead of the username on "
            "public surfaces; ignored while the account is age-shielded.",
        ),
    )
    op.add_column(
        "arena_users",
        sa.Column(
            "dta_troca_username",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp of the last username change; enforces the cooldown.",
        ),
    )
    op.add_column(
        "arena_users",
        sa.Column(
            "consent_generation",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Monotonic counter bumped on every parental-consent state "
            "transition (grant, revoke, guardian-email change); binds a "
            "revocation token to one consent epoch.",
        ),
    )

    connection = op.get_bind()
    _backfill_usernames(connection)
    connection.execute(sa.text(FIX_PUBLIC_PROFILE_INVARIANT))

    # Batch mode only here, and only because these three operations have no
    # bare ALTER TABLE form on SQLite: it recreates the table instead, while
    # PostgreSQL emits direct ALTER TABLE statements. The plain add_column calls
    # above need no such help, which is why they do not use it -- batch mode is
    # not house style, it is the workaround for exactly this.
    with op.batch_alter_table("arena_users") as batch_op:
        batch_op.create_unique_constraint("uq_arena_users_username", ["username"])
        batch_op.alter_column("username", existing_type=sa.String(64), nullable=False)
        batch_op.create_check_constraint(
            "ck_arena_users_public_profile_requires_ranking",
            "NOT (public_profile AND NOT ranking_visible)",
        )

    # Bound as a parameter, never as an INTERVAL literal: the earlier
    # 202605080001_arena_parental_consent migration used
    # `CURRENT_DATE - INTERVAL '18 years'`, which the SQLite test path cannot
    # execute.
    #
    # The bind carries an explicit Date type so SQLAlchemy renders it for each
    # backend. Passing the value raw does not work in both places: asyncpg is
    # strictly typed and rejects a string for a `date` column ("'str' object has
    # no attribute 'toordinal'"), while sqlite3's implicit date adapter is
    # deprecated. Typing the parameter lets the dialect decide, and is the only
    # form that runs on both. Both statements bind it the same way.
    #
    # One cutoff for both, so a birthday falling between them cannot leave a row
    # matched by neither or by both.
    cutoff = date.today() - relativedelta(years=18)
    preserve = sa.text(PRESERVE_ADULT_FULL_NAMES).bindparams(sa.bindparam("cutoff", type_=sa.Date()))
    connection.execute(preserve, {"cutoff": cutoff})
    shield = sa.text(SHIELD_EXISTING_MINORS).bindparams(sa.bindparam("cutoff", type_=sa.Date()))
    connection.execute(shield, {"cutoff": cutoff})


def downgrade() -> None:
    """Downgrade schema.

    The step 5 shield is not undone; see the module docstring.
    """
    with op.batch_alter_table("arena_users") as batch_op:
        batch_op.drop_constraint("ck_arena_users_public_profile_requires_ranking", type_="check")
        batch_op.drop_constraint("uq_arena_users_username", type_="unique")
        batch_op.drop_column("consent_generation")
        batch_op.drop_column("dta_troca_username")
        batch_op.drop_column("full_name_public")
        batch_op.drop_column("username")
