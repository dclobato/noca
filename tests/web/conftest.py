#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Web test fixtures."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from shared.services.rejudge_cooldown import reset_local_windows
from web.config import settings as web_settings
from web.services.export_rate_limit import (
    ADMIN_EXPORT_LIMITER,
    CONTEST_REPORT_LIMITER,
    TEAM_DOWNLOAD_LIMITER,
    UBERADMIN_EXPORT_LIMITER,
)
from web.services.password_confirm_throttle import PASSWORD_CONFIRM_LIMITER
from web.services.problem_export_rate_limit import PROBLEM_EXPORT_LIMITER
from web.services.public_rate_limits import LIVE_FEED_LIMITER, PROBLEM_SET_LIMITER
from web.services.user_read_rate_limit import USER_READ_LIMITER


@pytest.fixture(autouse=True)
def _neutralize_package_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Detach the Web suite from any locally configured package cache.

    ``NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH`` is read from the developer's own
    ``.env``, and setting it is the normal way to exercise the export cache by
    hand. Without this, doing so silently changes what the suite tests: routes
    that should rebuild serve a cached file instead, and because several fixtures
    use fixed contest slugs and problem ids, an archive left behind by one run is
    a cache hit in the next. That surfaced as a "409 when a statement file is
    missing" test getting a 200.

    A test that means to exercise the cache sets the path itself; running later,
    its own ``monkeypatch`` wins over this one.
    """
    monkeypatch.setattr(web_settings, "PUBLIC_PROBLEM_PACK_PATH", None)


@pytest.fixture(autouse=True)
def _reset_public_fallback_limiters() -> Generator[None]:
    """Clear the process-local public-read limiters around each test.

    The Web test apps carry no ``valkey_runtime``, so both public buckets run on
    their in-memory fallback, whose state would otherwise leak across the tests
    of one xdist worker and make repeated downloads order-dependent.
    """
    PROBLEM_SET_LIMITER._buckets.clear()
    LIVE_FEED_LIMITER._buckets.clear()
    PASSWORD_CONFIRM_LIMITER._buckets.clear()
    USER_READ_LIMITER._buckets.clear()
    PROBLEM_EXPORT_LIMITER._buckets.clear()
    ADMIN_EXPORT_LIMITER._buckets.clear()
    CONTEST_REPORT_LIMITER._buckets.clear()
    TEAM_DOWNLOAD_LIMITER._buckets.clear()
    UBERADMIN_EXPORT_LIMITER._buckets.clear()
    reset_local_windows()
    yield
    PROBLEM_SET_LIMITER._buckets.clear()
    LIVE_FEED_LIMITER._buckets.clear()
    PASSWORD_CONFIRM_LIMITER._buckets.clear()
    USER_READ_LIMITER._buckets.clear()
    PROBLEM_EXPORT_LIMITER._buckets.clear()
    ADMIN_EXPORT_LIMITER._buckets.clear()
    CONTEST_REPORT_LIMITER._buckets.clear()
    TEAM_DOWNLOAD_LIMITER._buckets.clear()
    UBERADMIN_EXPORT_LIMITER._buckets.clear()
