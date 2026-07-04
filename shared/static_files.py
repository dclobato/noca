#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import os

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

SHORT_CACHE_MAX_AGE_SECONDS = 300
"""Browser/CDN cache lifetime for CSS and JS, whose `@import`/`<script src>`
sub-resources are not individually cache-busted by the top-level
`?v={app_version}` query string. Keeps rollout staleness bounded to minutes
instead of Cloudflare's multi-hour default."""


class ShortCacheStaticFiles(StaticFiles):
    """`StaticFiles` that stamps a short `Cache-Control` on every response.

    Use for CSS/JS mounts reached through un-versioned `@import` chains or
    shared script includes, where the origin's default (Cloudflare's
    multi-hour) cache would otherwise let stale sub-files outlive a deploy.
    """

    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["cache-control"] = f"public, max-age={SHORT_CACHE_MAX_AGE_SECONDS}"
        return response
