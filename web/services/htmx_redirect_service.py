#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from fastapi import HTTPException, Request


def build_auth_redirect_exception(request: Request, location: str) -> HTTPException:
    """Return an auth redirect response appropriate for plain and HTMX requests.

    HTMX does not process response headers on 3xx responses. For HTMX requests
    we therefore return a non-3xx status carrying ``HX-Redirect`` so the browser
    performs a full-page navigation to the login screen instead of swapping the
    login HTML into a partial target.
    """
    if request.headers.get("HX-Request", "").lower() == "true":
        return HTTPException(
            status_code=401,
            headers={"HX-Redirect": location},
        )
    return HTTPException(status_code=302, headers={"Location": location})
