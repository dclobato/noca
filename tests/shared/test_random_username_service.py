#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for shared.services.random_username_service.

Every assertion here is a property that must hold for *every* draw, which is
why the repeated-draw loops cannot flake and need no seeding. They could not be
seeded in any case: ``generate_username`` draws from ``secrets``, which has no
seedable interface by design.
"""

from __future__ import annotations

import re

from shared.services.random_username_service import generate_username

_USERNAME_PATTERN = re.compile(r"^[a-z]+-[a-z]+-[0-9]{3}$")


def test_generate_username_matches_expected_shape() -> None:
    for _ in range(200):
        username = generate_username()
        assert _USERNAME_PATTERN.fullmatch(username), username


def test_generate_username_is_ascii_lowercase() -> None:
    for _ in range(200):
        username = generate_username()
        assert username == username.lower()
        username.encode("ascii")  # raises UnicodeEncodeError on non-ASCII


def test_generate_username_number_suffix_within_bounds() -> None:
    for _ in range(200):
        suffix = generate_username().rsplit("-", 1)[1]
        assert len(suffix) == 3, suffix
        assert 0 <= int(suffix) <= 999


def test_generate_username_varies() -> None:
    usernames = {generate_username() for _ in range(50)}
    assert len(usernames) > 1
