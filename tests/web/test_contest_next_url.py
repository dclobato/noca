#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The same-contest return destination a contest login may honour."""

from __future__ import annotations

import pytest

from web.services.session_service import safe_contest_next_url


@pytest.mark.parametrize(
    "candidate",
    [
        "/c/demo/admin/problems/p1/edit",
        "/c/demo/admin/problems/p1/edit?tab=statement",
        "/c/demo",
        "/c/demo/",
    ],
)
def test_same_contest_pages_are_honoured(candidate: str) -> None:
    assert safe_contest_next_url("demo", candidate, default=None) == candidate


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        "",
        "http://evil.example/c/demo/x",
        "//evil.example/c/demo/x",
        "/c/demo/\\evil",
        "/c/other/admin",
        "/c/demo-2/admin",
        "/uberadmin",
        "/c/demo/login",
        "/c/demo/login?next=/c/demo/x",
        "c/demo/x",
    ],
)
def test_everything_else_falls_back(candidate: str | None) -> None:
    assert safe_contest_next_url("demo", candidate, default="/c/demo") == "/c/demo"
    assert safe_contest_next_url("demo", candidate, default=None) is None
