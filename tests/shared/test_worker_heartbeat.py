#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from shared.services.worker_heartbeat import (
    heartbeat_is_fresh,
    heartbeat_loop,
    remove_heartbeat,
    touch_heartbeat,
)


def test_touch_creates_missing_parent_directories(tmp_path: Path) -> None:
    heartbeat = tmp_path / "nested" / "dir" / "heartbeat"

    touch_heartbeat(heartbeat)

    assert heartbeat.exists()


def test_remove_is_idempotent(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat"
    touch_heartbeat(heartbeat)

    remove_heartbeat(heartbeat)
    remove_heartbeat(heartbeat)

    assert not heartbeat.exists()


def test_freshness_boundary_is_inclusive(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat"
    touch_heartbeat(heartbeat)
    modified_at = heartbeat.stat().st_mtime

    assert heartbeat_is_fresh(heartbeat, max_age_seconds=30.0, now=modified_at + 30.0) is True
    assert heartbeat_is_fresh(heartbeat, max_age_seconds=30.0, now=modified_at + 30.001) is False


def test_missing_file_is_never_fresh(tmp_path: Path) -> None:
    assert heartbeat_is_fresh(tmp_path / "missing", max_age_seconds=30.0, now=0.0) is False


def test_unreadable_path_is_reported_stale_instead_of_raising(tmp_path: Path) -> None:
    """A path whose parent is a file makes stat() raise; the probe must not."""
    parent = tmp_path / "not-a-directory"
    parent.write_text("x", encoding="utf-8")

    assert heartbeat_is_fresh(parent / "heartbeat", max_age_seconds=30.0) is False


@pytest.mark.asyncio
async def test_loop_writes_immediately_and_stops_on_event(tmp_path: Path) -> None:
    """The first write lands well before the interval, and the event ends the loop.

    The write runs in a worker thread, so waiting a fixed number of event-loop
    turns is not enough on a loaded machine: the deadline below is what makes
    the test wait for the write rather than for the scheduler.
    """
    heartbeat = tmp_path / "heartbeat"
    stop_event = asyncio.Event()

    task = asyncio.create_task(heartbeat_loop(heartbeat, interval_seconds=300.0, stop_event=stop_event))
    try:
        deadline = asyncio.get_running_loop().time() + 10.0
        while not heartbeat.exists() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        # The interval is 300 s, so an existing file can only be the immediate write.
        assert heartbeat.exists()
    finally:
        stop_event.set()
        await asyncio.wait_for(task, timeout=10)

    assert task.done()
