#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Drive one streaming ASGI request with a controllable disconnect.

httpx's ``ASGITransport`` buffers a whole response body, so it cannot consume an
open-ended SSE stream. These tests therefore call the ASGI app directly: a
controllable ``receive`` channel delivers ``http.disconnect`` to end the stream,
and a ``send`` collector parses the real native SSE wire frames — the true route
boundary, with native framing, FastAPI's idle heartbeat, and structured
disconnect teardown all running for real.

The module name is underscore-prefixed so pytest does not collect it as a test.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

__all__ = ["ASGIStream", "parse_event"]


class ASGIStream:
    """One streaming ASGI request whose frames the test reads incrementally."""

    def __init__(
        self,
        app: Any,
        path: str,
        *,
        query_string: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        """Prepare (but do not start) a streaming GET.

        Args:
            app: The ASGI application under test.
            path: Request path, without a query string.
            query_string: Raw query string, without the leading ``?``.
            headers: Extra request headers.
        """
        self._app = app
        header_pairs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        self._scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query_string.encode(),
            "root_path": "",
            "scheme": "http",
            "headers": header_pairs,
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
        self._incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._chunks: asyncio.Queue[bytes] = asyncio.Queue()
        self._started = asyncio.Event()
        self.status: int | None = None
        self._buffer = b""
        self._task: asyncio.Task[None] | None = None

    async def _receive(self) -> dict[str, Any]:
        return await self._incoming.get()

    async def _send(self, message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            self.status = message["status"]
            self._started.set()
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            if body:
                self._chunks.put_nowait(body)

    async def __aenter__(self) -> ASGIStream:
        """Start the request and wait for the response to begin."""
        self._incoming.put_nowait({"type": "http.request", "body": b"", "more_body": False})
        self._task = asyncio.create_task(self._app(self._scope, self._receive, self._send))
        await asyncio.wait_for(self._started.wait(), timeout=2.0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Disconnect and await the request task, tolerating an early finish."""
        assert self._task is not None
        if not self._task.done():
            self._incoming.put_nowait({"type": "http.disconnect"})
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._task, timeout=2.0)

    async def disconnect(self) -> None:
        """Deliver ``http.disconnect`` and await the request task to finish."""
        assert self._task is not None
        self._incoming.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(self._task, timeout=2.0)

    def _drain_buffered(self, events: list[str]) -> None:
        """Split any complete frames already in the buffer into ``events``."""
        while b"\n\n" in self._buffer:
            raw, self._buffer = self._buffer.split(b"\n\n", 1)
            if raw.strip():
                events.append(raw.decode())

    async def read_events(self, count: int, *, timeout: float = 3.0) -> list[str]:
        """Read ``count`` complete SSE frames (each a ``\\n\\n``-terminated block)."""
        events: list[str] = []
        async with asyncio.timeout(timeout):
            while len(events) < count:
                self._buffer += await self._chunks.get()
                self._drain_buffered(events)
        return events


def parse_event(frame: str) -> dict[str, str]:
    """Parse an ``event:``/``data:``/``id:``/comment frame into a small dict."""
    parsed: dict[str, str] = {}
    for line in frame.splitlines():
        if line.startswith(":"):
            parsed["comment"] = line[1:].strip()
        elif ":" in line:
            key, _, value = line.partition(":")
            parsed[key.strip()] = value.strip()
    return parsed
