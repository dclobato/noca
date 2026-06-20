#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Startup readiness helpers.

Each NOCA runtime module calls ``wait_for_db`` and ``wait_for_valkey`` early in
its startup sequence so that transient unavailability of PostgreSQL or Valkey
produces clean retry log lines instead of raw exception tracebacks.

Both functions retry every ``_RETRY_INTERVAL_S`` seconds until the dependency
answers, or until ``timeout_s`` is exhausted.  Passing ``timeout_s=0`` skips
the wait entirely and raises immediately on the first failure (useful in unit
tests that want the old behaviour).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

_RETRY_INTERVAL_S = 5


async def wait_for_db(db_url: str, *, timeout_s: int, logger: logging.Logger) -> None:
    """Wait for PostgreSQL to accept connections, retrying every 5 seconds.

    Args:
        db_url: Full database URL (``postgresql+asyncpg://...`` or plain
            ``postgresql://...``).  The ``+asyncpg`` driver segment is stripped
            before passing the URL to asyncpg directly.
        timeout_s: Maximum seconds to wait.  0 means attempt once and raise
            immediately on failure.
        logger: Module logger used for progress messages.

    Raises:
        RuntimeError: PostgreSQL was not reachable within ``timeout_s`` seconds.
    """
    import asyncpg

    pg_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    attempt = 1

    while True:
        try:
            conn = await asyncpg.connect(pg_url, timeout=_RETRY_INTERVAL_S)
            await conn.close()
            logger.info("- PostgreSQL is reachable")
            return
        except Exception:
            pass

        remaining = deadline - loop.time()
        if remaining <= 0:
            raise RuntimeError(
                f"PostgreSQL not reachable after {timeout_s}s — "
                "check that the database server is running and the connection URL is correct."
            )

        wait = min(_RETRY_INTERVAL_S, remaining)
        logger.info(
            "Waiting for PostgreSQL (attempt %d, %.0fs remaining) — retrying in %.0fs",
            attempt,
            remaining,
            wait,
        )
        await asyncio.sleep(wait)
        attempt += 1


async def wait_for_valkey(valkey_url: str, *, timeout_s: int, logger: logging.Logger) -> None:
    """Wait for Valkey/Redis to answer a PING, retrying every 5 seconds.

    Args:
        valkey_url: Valkey connection URL (``redis://...``).
        timeout_s: Maximum seconds to wait.  0 means attempt once and raise
            immediately on failure.
        logger: Module logger used for progress messages.

    Raises:
        RuntimeError: Valkey was not reachable within ``timeout_s`` seconds.
    """
    import valkey.asyncio as aivalkey

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    attempt = 1

    while True:
        client: aivalkey.Valkey | None = None
        try:
            client = aivalkey.Valkey.from_url(valkey_url, socket_connect_timeout=_RETRY_INTERVAL_S)
            await client.ping()
            logger.info("- Valkey is reachable")
            return
        except Exception:
            pass
        finally:
            if client is not None:
                with contextlib.suppress(Exception):
                    await client.aclose()

        remaining = deadline - loop.time()
        if remaining <= 0:
            raise RuntimeError(
                f"Valkey not reachable after {timeout_s}s — "
                "check that the Valkey server is running and NOCA_VALKEY_URL is correct."
            )

        wait = min(_RETRY_INTERVAL_S, remaining)
        logger.info(
            "Waiting for Valkey (attempt %d, %.0fs remaining) — retrying in %.0fs",
            attempt,
            remaining,
            wait,
        )
        await asyncio.sleep(wait)
        attempt += 1
