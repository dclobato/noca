#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared asyncio helpers for aiassistant worker loops."""

from __future__ import annotations

import asyncio
import contextlib


async def interruptible_sleep(
    stop_event: asyncio.Event,
    trigger_event: asyncio.Event | None,
    timeout: float,
) -> bool:
    """Sleep for up to ``timeout`` seconds, waking early on any event.

    Args:
        stop_event: Shutdown signal; when set the caller should exit.
        trigger_event: Optional one-shot trigger; when set the caller should
            run its cycle immediately. Cleared before this function returns.
        timeout: Maximum sleep duration in seconds.

    Returns:
        ``True`` if ``stop_event`` fired (caller should exit immediately),
        ``False`` if the timeout elapsed or ``trigger_event`` fired (caller
        should run its cycle).
    """
    if trigger_event is None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=timeout)
        return stop_event.is_set()

    stop_task = asyncio.ensure_future(stop_event.wait())
    trigger_task = asyncio.ensure_future(trigger_event.wait())
    try:
        await asyncio.wait(
            {stop_task, trigger_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        for task in (stop_task, trigger_task):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    if trigger_event.is_set():
        trigger_event.clear()
    return stop_event.is_set()
