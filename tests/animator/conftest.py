#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Animator test fixtures."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from animator.dependencies import CONTROL_LOCKOUT_LIMITER, PUBLIC_RATE_LIMITER
from animator.routes.health import _fallback_health_limiter


@pytest.fixture(autouse=True)
def _reset_fallback_limiters() -> Generator[None]:
    """Clear the process-local rate limiters around each test.

    The animator test apps carry no Valkey runtime (or fakes without ``eval``),
    so the public-feed and health limiters run on their in-memory fallback,
    whose state would otherwise leak across the tests of one xdist worker.
    """
    PUBLIC_RATE_LIMITER._buckets.clear()
    _fallback_health_limiter._buckets.clear()
    CONTROL_LOCKOUT_LIMITER._buckets.clear()
    yield
    PUBLIC_RATE_LIMITER._buckets.clear()
    _fallback_health_limiter._buckets.clear()
    CONTROL_LOCKOUT_LIMITER._buckets.clear()
