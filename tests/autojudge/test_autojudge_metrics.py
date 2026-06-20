"""Tests for autojudge.metrics — exposition server and gauge helpers."""

from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest
from prometheus_client import generate_latest

from autojudge.metrics import start_metrics_server, update_pool_gauges, update_queue_depths

# ---------------------------------------------------------------------------
# HTTP server — GET /metrics
# ---------------------------------------------------------------------------


async def _http(port: int, request_line: bytes) -> bytes:
    """Open a connection, send a bare HTTP/1.0 request, return the response."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request_line + b"\r\n\r\n")
    await writer.drain()
    data = await asyncio.wait_for(reader.read(8192), timeout=3.0)
    writer.close()
    with suppress(Exception):
        await writer.wait_closed()
    return data


@pytest.mark.asyncio
async def test_metrics_server_get_metrics_returns_200() -> None:
    server = await start_metrics_server(0)
    port = server.sockets[0].getsockname()[1]
    try:
        response = await _http(port, b"GET /metrics HTTP/1.0")
        assert b"200 OK" in response
        assert b"# HELP" in response
        assert b"Content-Type" in response
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_metrics_server_other_path_returns_404() -> None:
    server = await start_metrics_server(0)
    port = server.sockets[0].getsockname()[1]
    try:
        response = await _http(port, b"GET /status HTTP/1.0")
        assert b"404" in response
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_metrics_server_post_returns_405() -> None:
    server = await start_metrics_server(0)
    port = server.sockets[0].getsockname()[1]
    try:
        response = await _http(port, b"POST /metrics HTTP/1.0")
        assert b"405" in response
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_metrics_server_root_path_returns_404() -> None:
    server = await start_metrics_server(0)
    port = server.sockets[0].getsockname()[1]
    try:
        response = await _http(port, b"GET / HTTP/1.0")
        assert b"404" in response
    finally:
        server.close()
        await server.wait_closed()


# ---------------------------------------------------------------------------
# Gauge helpers
# ---------------------------------------------------------------------------


def test_update_pool_gauges_sets_values() -> None:
    update_pool_gauges({"python3": 3, "cpp17": 1})
    output = generate_latest().decode()
    assert 'autojudge_pool_available_containers{language_id="python3"} 3.0' in output
    assert 'autojudge_pool_available_containers{language_id="cpp17"} 1.0' in output


def test_update_pool_gauges_overwrites_previous_value() -> None:
    update_pool_gauges({"java": 2})
    update_pool_gauges({"java": 0})
    output = generate_latest().decode()
    assert 'autojudge_pool_available_containers{language_id="java"} 0.0' in output


def test_update_queue_depths_sets_values() -> None:
    update_queue_depths({"pending": 7, "priority": 3, "profiling": 1, "inflight": 2})
    output = generate_latest().decode()
    assert 'autojudge_queue_depth{queue="pending"} 7.0' in output
    assert 'autojudge_queue_depth{queue="priority"} 3.0' in output
    assert 'autojudge_queue_depth{queue="profiling"} 1.0' in output
    assert 'autojudge_queue_depth{queue="inflight"} 2.0' in output


def test_update_queue_depths_overwrites_previous_value() -> None:
    update_queue_depths({"pending": 10})
    update_queue_depths({"pending": 0})
    output = generate_latest().decode()
    assert 'autojudge_queue_depth{queue="pending"} 0.0' in output


def test_generate_latest_includes_autojudge_metrics() -> None:
    """Smoke test: the default registry includes our metric families."""
    output = generate_latest().decode()
    assert "autojudge_jobs_dispatched_total" in output
    assert "autojudge_verdicts_total" in output
    assert "autojudge_compile_duration_seconds" in output
    assert "autojudge_run_wall_time_seconds" in output
    assert "autojudge_reaper_cycles_total" in output
    assert "autojudge_worker_slots_active" in output
