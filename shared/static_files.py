#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Static-file mount that closes the post-deploy stale-CSS window.

Every module links one stylesheet and one script bundle with `?v={app_version}`,
so a release busts *those* URLs. But `arena.css` and `contest.css` pull in the
whole design system through 40-odd `@import` rules whose URLs carry no version
at all -- including `/static/shared-css/common.css`, which owns the rendered-
Markdown rules. A browser therefore refetches the top-level file, sees the same
import URLs, and keeps serving its cached copies of everything underneath.

Bounding that with a short `max-age` (the previous approach, 300 s) left a
window in which a freshly deployed page was styled by the previous release's
CSS -- which is exactly what it looks like when table borders and heading gaps
disappear after an upgrade. `no-cache` removes the window instead of shrinking
it: the browser keeps the file but must revalidate, and `StaticFiles` answers an
unchanged one with a `304` carrying no body. The cost is one conditional request
per file per page load; the benefit is that a deploy is never half-applied.

`no-cache` is *not* `no-store`: nothing here forbids caching, it only requires
the cache to check first.
"""

import os

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

REVALIDATE_CACHE_CONTROL = "no-cache"
"""Cache directive for CSS and JS reached through un-versioned `@import` chains
and shared script includes: cache the file, but revalidate before reusing it."""


class RevalidatedStaticFiles(StaticFiles):
    """`StaticFiles` whose responses must be revalidated before reuse.

    Use for every CSS/JS mount. `StaticFiles` already emits an `ETag` and
    `Last-Modified` and answers a matching conditional request with `304`, so
    revalidation costs a header exchange rather than a transfer.
    """

    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        """Return the file (or its `304`) with the revalidation directive set.

        Args:
            full_path: Resolved path of the file being served.
            stat_result: Its stat, used for `ETag`/`Last-Modified`.
            scope: The ASGI scope of the request.
            status_code: Status the base class should use for a full response.

        Returns:
            Response: The base response, stamped with `Cache-Control`. The base
            class may already have turned it into a `304`; that response carries
            the header too, so a revalidating client is told the rule again.
        """
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["cache-control"] = REVALIDATE_CACHE_CONTROL
        return response
