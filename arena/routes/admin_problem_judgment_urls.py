#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Where the Arena judgment-data pages live.

Its own module because almost every problem route needs to redirect to one of
these pages, and the module that *builds* their context imports half the problem
package -- importing that just to resolve a URL would tie the routes in a knot.
"""

from __future__ import annotations

from fastapi import Request

#: Endpoint name per judgment page key.
PAGE_ENDPOINTS = {
    "test-cases": "arena_admin_problem_judgment_cases",
    "validator": "arena_admin_problem_judgment_validator",
    "interactions": "arena_admin_problem_judgment_interactions",
}


def with_query(url: str, query: str) -> str:
    """Attach a raw, already-encoded query string to ``url``.

    Every URL built by ``url_for`` carries no query string of its own, so a plain
    append is safe: this is never called on a URL that might already have one.
    Mirrors the plain ``{% if request.url.query %}?{{ request.url.query }}{% endif
    %}`` pattern ``problem_list.html`` already uses on its own row links, so the
    problem list's page, filters, and sort survive every hop between the list,
    the definition editor, and the judgment-data pages.

    Args:
        url: The destination URL.
        query: The raw query string to carry forward, or "".

    Returns:
        str: ``url`` unchanged if ``query`` is empty, otherwise with it attached.
    """
    return f"{url}?{query}" if query else url


def judgment_page_url(
    request: Request,
    problem_id: str,
    page: str = "test-cases",
    *,
    query: str | None = None,
) -> str:
    """Return the URL of one judgment page.

    Args:
        request: The active request.
        problem_id: The problem being edited.
        page: A judgment page key.
        query: The query string to attach, or "" for none. Defaults to the
            current request's own query string, since almost every caller is
            either bouncing an author between judgment pages (whose query is
            exactly what should carry forward) or completing a POST whose
            action URL was itself built with that query attached.

    Returns:
        str: The page's URL.
    """
    effective_query = request.url.query if query is None else query
    return with_query(str(request.url_for(PAGE_ENDPOINTS[page], problem_id=problem_id)), effective_query)
