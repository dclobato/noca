#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The deterministic fallback avatar is seeded on the username.

Seeding it on the email address made the public avatar a confirmation oracle:
the generator is deterministic and the image is served to anyone, so a guessed
address could be rendered and compared against a user's avatar to test whether
that person holds that mailbox. These tests pin the seed to the handle, which
is public by design.
"""

from __future__ import annotations

from arena.models.arena_user_media import _generated_avatar
from arena.models.arena_users import ArenaUser


def _user(*, username: str, email: str) -> ArenaUser:
    """Build an unsaved user with no stored photo."""
    user = ArenaUser()
    user.username = username
    user.email_normalizado = email
    user.com_foto = False
    return user


def test_fallback_avatar_is_seeded_on_the_username() -> None:
    user = _user(username="tigre-astuto-074", email="someone@example.com")

    data, mime = user.avatar

    assert mime == "image/svg+xml"
    assert data == _generated_avatar("tigre-astuto-074")


def test_foto_falls_back_to_the_same_avatar() -> None:
    user = _user(username="tigre-astuto-074", email="someone@example.com")

    assert user.foto == user.avatar


def test_avatar_does_not_depend_on_the_email_address() -> None:
    """Two accounts sharing a handle but not an address render identically.

    This is the oracle being closed: the image carries no information about the
    mailbox behind the account.
    """
    one = _user(username="same-handle-001", email="first@example.com")
    other = _user(username="same-handle-001", email="second@example.com")

    assert one.avatar == other.avatar


def test_avatar_is_not_seeded_on_the_email_address() -> None:
    """The rendered image must differ from what the old email seed produced."""
    user = _user(username="tigre-astuto-074", email="someone@example.com")

    assert user.avatar[0] != _generated_avatar("someone@example.com")


def test_distinct_handles_render_distinct_avatars() -> None:
    one = _user(username="tigre-astuto-074", email="shared@example.com")
    other = _user(username="lobo-calmo-443", email="shared@example.com")

    assert one.avatar != other.avatar
