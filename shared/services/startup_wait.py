#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Startup readiness helpers.

Each NOCA runtime module calls ``wait_for_db`` and ``wait_for_valkey`` early in
its startup sequence so that transient unavailability of PostgreSQL or Valkey
produces clean retry log lines instead of raw exception tracebacks. The two
HTTP servers additionally call ``wait_for_mailer`` once their Valkey runtime is
up, because they cannot send email on their own.

All three retry every ``_RETRY_INTERVAL_S`` seconds until the dependency
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


async def wait_for_mailer(valkey_runtime: object, *, timeout_s: int, logger: logging.Logger) -> None:
    """Wait until at least one mailer worker is live, retrying every 5 seconds.

    Web and Arena never send email themselves: every message is queued for the
    ``noca-mailer`` worker. A deployment without one accepts mail that is never
    delivered, so the two HTTP servers refuse to *start* until a mailer has
    published its presence marker (``WorkerClass.MAILER``). This is a startup
    dependency only -- individual sends never check mailer liveness, which is
    what lets a mailer restart without refusing a single request.

    Args:
        valkey_runtime: The module's started ``ValkeyRuntime`` (the presence
            registry is read through it).
        timeout_s: Maximum seconds to wait. 0 means check once and raise
            immediately when no mailer is live.
        logger: Module logger used for progress messages.

    Raises:
        RuntimeError: No mailer worker became live within ``timeout_s`` seconds.
    """
    from shared.services.valkey_service.worker_presence import WorkerClass, list_workers

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    attempt = 1

    while True:
        online = 0
        try:
            workers = await list_workers(valkey_runtime, WorkerClass.MAILER)
            online = sum(1 for worker in workers if worker.online)
        except Exception as exc:  # noqa: BLE001 - a presence read failure is just "not yet"
            logger.warning("Could not read mailer presence: %s", exc)
        if online:
            logger.info("- Mailer worker is live (%d online)", online)
            return

        remaining = deadline - loop.time()
        if remaining <= 0:
            raise RuntimeError(
                f"No mailer worker is live after {timeout_s}s — "
                "start noca-mailer: Web and Arena queue every email for it and cannot send on their own."
            )

        wait = min(_RETRY_INTERVAL_S, remaining)
        logger.info(
            "Waiting for a mailer worker (attempt %d, %.0fs remaining) — retrying in %.0fs",
            attempt,
            remaining,
            wait,
        )
        await asyncio.sleep(wait)
        attempt += 1
