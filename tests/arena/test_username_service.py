#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for arena.services.username_service."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from werkzeug.security import generate_password_hash

from arena.models.arena_users import ArenaUser
from arena.services import username_service
from arena.services.username_service import (
    USERNAME_MAX_LENGTH,
    USERNAME_PATTERN,
    UsernameError,
    generate_unique_username,
    is_username_available,
    normalize_username,
    validate_username,
)
from shared.enumerations import ArenaRole

_TEST_PASSWORD = "TestPass1!"


async def _make_user(session: AsyncSession, *, email: str, username: str | None = None) -> ArenaUser:
    """Persist a minimal Arena user, optionally with a chosen handle."""
    values: dict[str, object] = {
        "id": str(uuid.uuid4()),
        "nome": "Test User",
        "email_normalizado": email,
        "password_hash": generate_password_hash(_TEST_PASSWORD, method="pbkdf2:sha256:1000"),
        "role": ArenaRole.ARENA_USER,
        "ativo": True,
        "email_confirmado": True,
        "dta_nascimento": date(2000, 1, 1),
        "consentimento_responsavel": True,
        "com_foto": False,
        "usa_2fa": False,
        "precisa_trocar_senha": False,
        "session_version": 0,
    }
    if username is not None:
        values["username"] = username
    user = ArenaUser(**values)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


# ----------------------------------------------------------------------
# normalization
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Handle  ", "handle"),
        ("MiXeDCase", "mixedcase"),
        ("ＵＳＥＲ", "user"),  # NFKC folds fullwidth forms
        ("Straße", "strasse"),  # casefold, not lower()
    ],
)
def test_normalize_username_canonicalizes(raw: str, expected: str) -> None:
    assert normalize_username(raw) == expected


def test_normalize_username_accepts_fullwidth_input() -> None:
    """A fullwidth handle normalizes onto its ASCII twin and validates."""
    assert validate_username("ｕｓｅｒ１２３") == "user123"


def test_validate_username_rejects_cyrillic_homoglyph() -> None:
    """Cyrillic look-alikes are refused by the charset, not by normalization.

    NFKC deliberately does not map Cyrillic 'а' (U+0430) onto Latin 'a' -- they
    are different letters, not compatibility variants. The pattern's [a-z0-9_-]
    restriction is what stops a homoglyph impersonating an existing handle.
    """
    cyrillic = "аdmin"  # 'а' + "dmin"
    assert normalize_username(cyrillic) == cyrillic
    with pytest.raises(UsernameError):
        validate_username(cyrillic)


# ----------------------------------------------------------------------
# validation
# ----------------------------------------------------------------------


@pytest.mark.parametrize("value", ["abc", "a-b", "a_b", "user123", "a" * USERNAME_MAX_LENGTH])
def test_validate_username_accepts_valid_handles(value: str) -> None:
    assert validate_username(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "ab",  # too short
        "a" * (USERNAME_MAX_LENGTH + 1),  # too long
        "-leading",
        "trailing-",
        "_leading",
        "trailing_",
        "has space",
        "has.dot",
        "has@sign",
        "",
    ],
)
def test_validate_username_rejects_malformed_handles(value: str) -> None:
    with pytest.raises(UsernameError):
        validate_username(value)


@pytest.mark.parametrize("value", ["admin", "ADMIN", "Arena", "profile", "ranking", "auth", "null"])
def test_validate_username_rejects_reserved_handles(value: str) -> None:
    with pytest.raises(UsernameError):
        validate_username(value)


def test_validate_username_rejects_uuid_shaped_handles() -> None:
    """A UUID-shaped handle could be mistaken for a /profile/{user_id} path."""
    with pytest.raises(UsernameError):
        validate_username(str(uuid.uuid4()))


def test_no_reserved_name_is_itself_invalid() -> None:
    """Every reserved word is a handle someone could otherwise have claimed.

    A reserved entry that the pattern already rejects is dead weight and hints
    the list has drifted away from what it is protecting.
    """
    for reserved in username_service.RESERVED_USERNAMES:
        assert USERNAME_PATTERN.fullmatch(reserved), reserved


# ----------------------------------------------------------------------
# generation
# ----------------------------------------------------------------------


def test_generated_handles_always_validate() -> None:
    """Every drawn handle must survive the validator it will be stored through.

    This is a property of all output rather than of a sampled subset, so the
    loop cannot flake; `generate_username` draws from `secrets` and cannot be
    seeded in any case.
    """
    from shared.services.random_username_service import generate_username

    for _ in range(300):
        candidate = generate_username()
        assert validate_username(candidate) == candidate
        assert len(candidate) <= USERNAME_MAX_LENGTH


@pytest.mark.asyncio
async def test_is_username_available_reports_taken_handles(session: AsyncSession) -> None:
    user = await _make_user(session, email="taken@example.com", username="taken-handle-001")

    assert await is_username_available(session, "free-handle-001") is True
    assert await is_username_available(session, "taken-handle-001") is False
    assert await is_username_available(session, "taken-handle-001", exclude_user_id=user.id) is True


@pytest.mark.asyncio
async def test_generate_unique_username_avoids_existing_handles(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generation retries past a taken handle rather than returning it."""
    await _make_user(session, email="first@example.com", username="tigre-astuto-001")

    draws = iter(["tigre-astuto-001", "tigre-astuto-002"])
    monkeypatch.setattr(username_service, "generate_username", lambda: next(draws))

    assert await generate_unique_username(session) == "tigre-astuto-002"


@pytest.mark.asyncio
async def test_generate_unique_username_terminates_on_a_constant_generator(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A generator that only ever draws a taken handle still terminates.

    The final attempt appends a four-digit suffix, so the result is unique even
    though every plain draw collided.
    """
    await _make_user(session, email="constant@example.com", username="tigre-astuto-001")
    monkeypatch.setattr(username_service, "generate_username", lambda: "tigre-astuto-001")

    result = await generate_unique_username(session, max_attempts=3)

    assert result != "tigre-astuto-001"
    assert result.startswith("tigre-astuto-001-")
    assert validate_username(result) == result


@pytest.mark.asyncio
async def test_unique_constraint_is_the_real_guarantee(session: AsyncSession) -> None:
    """Availability is advisory; the database refuses the duplicate."""
    await _make_user(session, email="one@example.com", username="shared-handle-001")

    with pytest.raises(IntegrityError):
        await _make_user(session, email="two@example.com", username="shared-handle-001")
    await session.rollback()


@pytest.mark.asyncio
async def test_username_uniqueness_is_case_insensitive_by_canonicalization(session: AsyncSession) -> None:
    """Two handles differing only in case cannot both exist.

    Arena stores the canonical form and has no functional lower() index, so the
    protection comes from every write path normalizing first -- which is why
    `validate_username` is the only sanctioned door.
    """
    await _make_user(session, email="cased@example.com", username="cased-handle-001")

    assert validate_username("Cased-Handle-001") == "cased-handle-001"
    assert await is_username_available(session, validate_username("Cased-Handle-001")) is False


@pytest.mark.asyncio
async def test_row_without_an_explicit_username_gets_a_generated_one(session: AsyncSession) -> None:
    """The column default keeps existing construction sites working.

    Dozens of tests build an ArenaUser without naming a handle; the Python-side
    default is what makes that legal against a NOT NULL column.
    """
    user = await _make_user(session, email="defaulted@example.com")

    assert user.username
    assert validate_username(user.username) == user.username
