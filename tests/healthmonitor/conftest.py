#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Health monitor test fixtures."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from healthmonitor.dependencies import HEALTH_RATE_LIMITER, PUBLIC_RATE_LIMITER


@pytest.fixture(autouse=True)
def _reset_fallback_limiters() -> Generator[None]:
    """Clear the process-local rate limiters around each test.

    The health monitor test apps carry fake runtimes without ``eval``, so both
    limiters run on their in-memory fallback, whose state would otherwise leak
    across the tests of one xdist worker and make route tests order-dependent.
    """
    PUBLIC_RATE_LIMITER._buckets.clear()
    HEALTH_RATE_LIMITER._buckets.clear()
    yield
    PUBLIC_RATE_LIMITER._buckets.clear()
    HEALTH_RATE_LIMITER._buckets.clear()
