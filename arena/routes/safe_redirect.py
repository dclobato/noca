#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Redirect back to where a form came from without trusting the ``Referer``.

A handful of admin actions live inside a filtered list and want to return the
admin to that same page, filters and all. The browser's ``Referer`` header
knows that page, but it is caller-controlled: relaying it verbatim turns the
route into an open redirect. This module reduces it to the one thing the route
can safely use -- a same-origin path with its query string -- and answers a
named fallback for anything else.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request


def same_origin_referer_path(request: Request, *, fallback: str) -> str:
    """Return the ``Referer``'s path and query when it is this origin's, else *fallback*.

    Accepted: an absolute URL whose scheme and host (with port) equal the
    request's own, or a bare local path. Refused: another origin, a
    scheme-relative ``//host`` value, a value with no path, or no header at
    all. The fragment is dropped since it never reaches the server anyway.

    Args:
        request: The incoming request whose origin the referer must match.
        fallback: The URL to answer when the header is missing or unusable.

    Returns:
        A path beginning with a single ``/`` (plus ``?query`` when present), or *fallback*.
    """
    referer = request.headers.get("referer", "")
    if not referer:
        return fallback
    parts = urlsplit(referer)
    if parts.scheme or parts.netloc:
        own = request.url
        if parts.scheme != own.scheme or parts.netloc != own.netloc:
            return fallback
    path = parts.path
    if not path.startswith("/") or path.startswith("//"):
        return fallback
    return f"{path}?{parts.query}" if parts.query else path
