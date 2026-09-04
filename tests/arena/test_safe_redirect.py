#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""``same_origin_referer_path``: the ``Referer`` is only ever reduced to a local path."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from arena.routes.safe_redirect import same_origin_referer_path

FALLBACK = "/admin/dashboard/submissions"


def _request(referer: str | None) -> Request:
    headers = [(b"host", b"arena.example.org")]
    if referer is not None:
        headers.append((b"referer", referer.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "scheme": "https",
        "path": "/admin/dashboard/submissions/x/reenqueue",
        "query_string": b"",
        "headers": headers,
        "server": ("arena.example.org", 443),
    }
    return Request(scope)


@pytest.mark.parametrize(
    ("referer", "expected"),
    [
        (None, FALLBACK),
        ("", FALLBACK),
        (
            "https://arena.example.org/admin/dashboard/submissions?page=3&status_filter=FAILED",
            "/admin/dashboard/submissions?page=3&status_filter=FAILED",
        ),
        ("https://arena.example.org/x#frag", "/x"),
        ("/admin/dashboard/submissions?page=2", "/admin/dashboard/submissions?page=2"),
        ("https://evil.example.net/admin/dashboard/submissions", FALLBACK),
        ("http://arena.example.org/admin/dashboard/submissions", FALLBACK),
        ("https://arena.example.org:8443/admin", FALLBACK),
        ("//evil.example.net/admin", FALLBACK),
        ("https://arena.example.org", FALLBACK),
        ("javascript:alert(1)", FALLBACK),
        ("admin/relative", FALLBACK),
    ],
)
def test_only_a_same_origin_path_survives(referer: str | None, expected: str) -> None:
    assert same_origin_referer_path(_request(referer), fallback=FALLBACK) == expected
