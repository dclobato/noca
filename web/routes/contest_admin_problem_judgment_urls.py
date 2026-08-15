#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Where the Contest judgment-data pages live.

Its own module because almost every problem route needs to redirect to one of
these pages, and the module that *builds* their context imports half the problem
package -- importing that just to resolve a URL would tie the routes in a knot.
"""

from __future__ import annotations

from fastapi import Request

#: Endpoint name per judgment page key.
PAGE_ENDPOINTS = {
    "test-cases": "problem_judgment_cases",
    "validator": "problem_judgment_validator",
    "interactions": "problem_judgment_interactions",
}


def judgment_page_url(request: Request, slug: str, problem_id: str, page: str = "test-cases") -> str:
    """Return the URL of one judgment page.

    Args:
        request: The active request.
        slug: The contest login slug.
        problem_id: The problem being edited.
        page: A judgment page key.

    Returns:
        str: The page's URL.
    """
    return str(request.url_for(PAGE_ENDPOINTS[page], slug=slug, problem_id=problem_id))
