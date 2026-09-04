#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena username normalization, validation, and uniqueness.

Drawing a handle is not this module's job -- that is
``shared.services.random_username_service.generate_username``, which knows the
word lists. This module owns the Arena-specific policy around it: what a handle
may look like, which handles are refused, and how one is made unique against
the ``arena_users`` table.

Two rules are worth stating outright because they are easy to get wrong later:

- **The username is not a login identifier.** Email remains the only way to log
  in. Accepting a handle at ``/auth/login`` would make the pseudonym an account
  enumeration vector, which is precisely what the handle exists to prevent.
- **Uniqueness is guaranteed by the database, not by this module.**
  :func:`is_username_available` is a TOCTOU: another transaction may take the
  name between the check and the insert. The ``UNIQUE`` constraint is the real
  guarantee, so every caller must be prepared to catch ``IntegrityError``.

Residual homoglyph confusability is deliberately out of scope. NFKC folding plus
the ``[a-z0-9_-]`` charset rules out cross-script impersonation (Cyrillic ``а``,
Greek ``ο``), but ``0`` versus ``o`` and ``-`` versus ``_`` remain
distinguishable only by careful reading. Closing that would need a skeleton-form
index, which is a different feature.

**Why this is hand-rolled rather than a dependency.** ``python-usernames`` was
evaluated twice -- once when the handle was only ever server-drawn, and again
once a user could choose one. It is still not adopted: its pattern admits ``.``
where :data:`USERNAME_PATTERN` does not, and its blocklist is English-language
profanity while Arena's primary audience is pt-BR. It supplies none of the NFKC
canonicalization, the route-derived reserved set, the UUID-shape rejection, the
uniqueness check, or the cooldown. Adopting it would install a *second*
validator that disagrees with this one -- which is the drift this module exists
to prevent. ``better-profanity`` was rejected as unmaintained (last release
2020) and likewise English-only.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from shared.db_schema.arena import arena_users
from shared.services.random_username_service import generate_username

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from arena.models.arena_users import ArenaUser

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 64

#: A handle is lowercase alphanumeric with interior hyphens and underscores,
#: and must begin and end alphanumerically. The bounds are enforced separately
#: so that a too-short and a malformed handle produce different messages.
USERNAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{1,62}[a-z0-9])?$")

#: Matches the canonical 8-4-4-4-12 UUID form. Arena addresses public profiles
#: as ``/profile/{user_id}``, so a UUID-shaped handle could be mistaken for
#: someone else's identifier.
_UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

MAX_GENERATION_ATTEMPTS = 8

#: Handles nobody may hold. The first group mirrors the path segments Arena
#: serves (kept in step with ``arena.dependencies.access_control`` by hand
#: rather than by import: those names are private to that module, and importing
#: a dependency module from a service would invert the layering). The second
#: group is impersonation bait.
RESERVED_USERNAMES: frozenset[str] = frozenset(
    {
        # route segments
        "api",
        "assets",
        "auth",
        "dashboard",
        "health",
        "help",
        "legal",
        "login",
        "logout",
        "problems",
        "profile",
        "ranking",
        "settings",
        "signup",
        # impersonation
        "admin",
        "administrator",
        "anonymous",
        "arena",
        "deleted",
        "judge",
        "moderator",
        "noca",
        "null",
        "root",
        "staff",
        "support",
        "system",
        "undefined",
    }
)


class UsernameError(ValueError):
    """Raised when a submitted username is not acceptable."""


class UsernameTakenError(UsernameError):
    """Raised when a well-formed handle is already held by another account.

    Distinguished from a plain :class:`UsernameError` so a caller can answer
    ``409`` for a conflict and ``400`` for a handle that could never be accepted,
    without inspecting the message text.
    """

    def __init__(self) -> None:
        """Initialise with the standard conflict message."""
        super().__init__("That username is already taken.")


class UsernameCooldownError(UsernameError):
    """Raised when a handle is changed again before the cooldown has elapsed.

    Carries the remaining whole days so a caller can render "you may change it
    again in N days" without re-deriving the arithmetic and risking a different
    rounding rule than the one the check used.

    Attributes:
        remaining_days: Whole days the caller must still wait.
    """

    def __init__(self, remaining_days: int) -> None:
        """Initialise with the number of days still to wait."""
        super().__init__(f"You can change your username again in {remaining_days} day(s).")
        self.remaining_days = remaining_days


def normalize_username(raw: str) -> str:
    """Reduce a submitted handle to its canonical stored form.

    NFKC folds compatibility forms -- fullwidth ``ｕｓｅｒ`` becomes ``user`` --
    so that two inputs which render identically cannot both be stored. Note
    that NFKC does *not* map Cyrillic or Greek look-alikes onto Latin letters;
    those are refused by :data:`USERNAME_PATTERN` instead.

    The stored form *is* the display form: Arena keeps no separate canonical
    column and no functional ``lower()`` index, so a handle has no display
    casing. That is the accepted cost of having exactly one place where the
    invariant lives.

    Args:
        raw: The handle as submitted.

    Returns:
        str: The canonical lowercase form.
    """
    return unicodedata.normalize("NFKC", raw).strip().casefold()


def validate_username(raw: str) -> str:
    """Normalize a submitted handle and confirm it is acceptable.

    Args:
        raw: The handle as submitted.

    Returns:
        str: The canonical form, safe to store.

    Raises:
        UsernameError: If the handle is too short or too long, contains
            characters outside ``[a-z0-9_-]``, does not begin and end
            alphanumerically, is reserved, or is shaped like a UUID.
    """
    username = normalize_username(raw)
    if len(username) < USERNAME_MIN_LENGTH:
        raise UsernameError(f"Username must be at least {USERNAME_MIN_LENGTH} characters long.")
    if len(username) > USERNAME_MAX_LENGTH:
        raise UsernameError(f"Username must be at most {USERNAME_MAX_LENGTH} characters long.")
    if not USERNAME_PATTERN.fullmatch(username):
        raise UsernameError(
            "Username may contain only lowercase letters, digits, hyphens and "
            "underscores, and must start and end with a letter or digit."
        )
    if username in RESERVED_USERNAMES:
        raise UsernameError("That username is reserved.")
    if _UUID_PATTERN.fullmatch(username):
        raise UsernameError("That username is reserved.")
    return username


async def is_username_available(
    session: AsyncSession,
    username: str,
    *,
    exclude_user_id: str | None = None,
) -> bool:
    """Report whether a handle is currently unclaimed.

    This is advisory only. The answer can be stale by the time the caller acts
    on it; see this module's docstring.

    Args:
        session: Open Arena database session.
        username: The canonical handle to look for.
        exclude_user_id: A user whose own handle should not count as a clash,
            so that re-submitting an unchanged handle is not a conflict.

    Returns:
        bool: True when no other user holds the handle.
    """
    query = select(func.count()).select_from(arena_users).where(arena_users.c.username == username)
    if exclude_user_id is not None:
        query = query.where(arena_users.c.id != exclude_user_id)
    return (await session.scalar(query) or 0) == 0


async def generate_unique_username(
    session: AsyncSession,
    *,
    max_attempts: int = MAX_GENERATION_ATTEMPTS,
) -> str:
    """Draw a pseudonymous handle that is not already taken.

    Each attempt draws from the shared generator and checks availability. The
    final attempt appends a four-digit suffix, which makes termination
    unconditional: even if every plain handle the generator can produce were
    taken, the suffixed form draws from a space 10 000 times larger. The caller
    still has to handle ``IntegrityError``, because the check is a TOCTOU.

    Args:
        session: Open Arena database session.
        max_attempts: How many handles to try before falling back to the
            suffixed form.

    Returns:
        str: A handle that was unclaimed a moment ago.
    """
    candidate = generate_username()
    for _ in range(max(max_attempts - 1, 0)):
        if await is_username_available(session, candidate):
            return candidate
        candidate = generate_username()
    if await is_username_available(session, candidate):
        return candidate
    return f"{generate_username()}-{secrets.randbelow(10_000):04d}"


def username_cooldown_remaining(user: ArenaUser, *, now: datetime | None = None) -> int:
    """Return the whole days left before this user may change their handle again.

    The cooldown exists for the age shield rather than for tidiness: the handle
    is the pseudonym a 13-17 year-old is published under, and unlimited churn
    would let an observer watching the ranking correlate a user's old and new
    handles and undo the pseudonymity.

    Args:
        user: The Arena user whose last change is being measured.
        now: Instant to measure from; defaults to the current UTC time.

    Returns:
        int: Days still to wait, rounded up, or ``0`` when the cooldown is
        disabled (``NOCA_ARENA_USERNAME_CHANGE_COOLDOWN_DAYS = 0``), when the
        user has never changed their handle, or when the window has elapsed.
    """
    cooldown_days = settings.USERNAME_CHANGE_COOLDOWN_DAYS
    if cooldown_days <= 0 or user.dta_troca_username is None:
        return 0
    last_change = user.dta_troca_username
    if last_change.tzinfo is None:
        last_change = last_change.replace(tzinfo=UTC)
    available_at = last_change + timedelta(days=cooldown_days)
    remaining = available_at - (now or datetime.now(UTC))
    if remaining <= timedelta(0):
        return 0
    return -(-remaining // timedelta(days=1))


async def change_username(
    session: AsyncSession,
    user: ArenaUser,
    raw: str,
    *,
    bypass_cooldown: bool = False,
    clear_cooldown: bool = False,
) -> str:
    """Change a user's handle, enforcing validation, the cooldown, and uniqueness.

    Resubmitting the handle the user already holds is a no-op that returns
    early **without** stamping ``dta_troca_username``: saving an unchanged form
    must not start a fresh 30-day cooldown.

    **A rename normally starts the cooldown, including an administrative one.**
    That default protects the case an admin rename exists for: a handle renamed
    off a report must not be renamed straight back. But it is the wrong answer
    when the admin is fixing a typo or acting on the user's own request, since it
    locks that user out of choosing for a full window. The server cannot tell the
    two apart, so ``clear_cooldown`` lets the caller say which it is.

    ``IntegrityError`` is deliberately not caught here. The availability check
    is advisory -- another transaction may take the name between the check and
    the flush -- so the ``UNIQUE`` constraint is the real guarantee, and every
    caller maps that failure to ``409`` rather than letting it surface as a
    ``500``. The cooldown check is likewise a read-then-write with no row lock:
    two concurrent requests from the same user could both pass the window
    check, whose only consequence is a re-stamped timestamp, so the contention a
    lock would add is not worth buying that out.

    Args:
        session: Open Arena database session.
        user: The Arena user being renamed.
        raw: The handle as submitted.
        bypass_cooldown: True for an administrative rename, which overrides the
            waiting period. Never true for a self-service change.
        clear_cooldown: True to leave the user free to rename immediately, by
            clearing ``dta_troca_username`` instead of stamping it. Only an
            administrative rename passes this, and only when told to.

    Returns:
        str: The canonical handle now stored.

    Raises:
        UsernameError: If the handle is malformed or reserved.
        UsernameTakenError: If another account already holds the handle.
        UsernameCooldownError: If the cooldown has not elapsed and it is not
            being bypassed.
    """
    username = validate_username(raw)
    if username == user.username:
        return username
    if not bypass_cooldown:
        remaining = username_cooldown_remaining(user)
        if remaining > 0:
            raise UsernameCooldownError(remaining)
    if not await is_username_available(session, username, exclude_user_id=user.id):
        raise UsernameTakenError
    user.username = username
    user.bump_avatar_revision()
    user.dta_troca_username = None if clear_cooldown else datetime.now(UTC)
    await session.flush()
    return username
