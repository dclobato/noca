#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Process-wide ceiling on open animator SSE clients.

The Valkey-backed per-IP lease (:mod:`shared.services.sse_connection_limit`)
fails open by design, so this in-process gauge is the last line of defense that
does not depend on Valkey at all: one counter shared by ``/events`` and
``/reveal/events``, refused with ``503`` when full. It lives here rather than in
``AnimatorEventStream.register()`` because the reveal stream has no registry of
its own -- one gauge in the dependency guards both streams.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from fastapi import HTTPException

__all__ = ["SseCapacity"]

RETRY_AFTER_SECONDS = 5


class SseCapacity:
    """A bounded gauge of open SSE clients in this process."""

    def __init__(self, max_clients: int) -> None:
        """Create the gauge.

        Args:
            max_clients: Ceiling on simultaneously held slots; clamped to ``>= 1``.
        """
        self._max = max(1, max_clients)
        self._active = 0

    @property
    def active(self) -> int:
        """Number of slots currently held."""
        return self._active

    @property
    def max_clients(self) -> int:
        """The configured ceiling."""
        return self._max

    @contextlib.asynccontextmanager
    async def slot(self, detail: str = "Too many open event streams on this server.") -> AsyncIterator[None]:
        """Hold one slot for the duration of the block.

        Raises:
            HTTPException: ``503`` with ``Retry-After`` when the ceiling is reached.
        """
        if self._active >= self._max:
            raise HTTPException(status_code=503, detail=detail, headers={"Retry-After": str(RETRY_AFTER_SECONDS)})
        self._active += 1
        try:
            yield
        finally:
            self._active = max(0, self._active - 1)
