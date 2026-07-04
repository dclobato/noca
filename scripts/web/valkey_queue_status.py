#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Show the current state of the judgment queues and job hashes in Valkey.

Usage:
    uv run scripts/web/valkey_queue_status.py           # show queue status
    uv run scripts/web/valkey_queue_status.py --clear   # delete all queues and job hashes
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any, cast

import valkey.asyncio as aivalkey

from shared.services.valkey_service import (
    QUEUE_KEYS,
    QUEUE_UNKNOWN_CONTEST,
    get_all_contest_queue_metrics,
)
from web.config import settings

UNKNOWN_CONTEST_BUCKET = QUEUE_UNKNOWN_CONTEST


async def _scan_job_hash_keys(client: aivalkey.Valkey) -> list[str]:
    keys: list[str] = []
    cursor = 0
    pattern = f"{settings.queue_job_hash_prefix}:*"
    while True:
        cursor, batch = await client.scan(cursor=cursor, match=pattern, count=500)
        keys.extend(batch)
        if cursor == 0:
            break
    return keys


async def collect_queue_metrics(client: aivalkey.Valkey) -> dict[str, Any]:
    # Read raw queue IDs for the verbose listing section in render_queue_metrics.
    queue_ids: dict[str, list[str]] = {}
    for queue_key in QUEUE_KEYS:
        items = cast(list[str], await cast(Any, client.lrange(queue_key, 0, -1)))
        queue_ids[queue_key] = items

    # Delegate aggregation to the shared service helper.
    agg = await get_all_contest_queue_metrics(client)
    assert agg is not None  # raw client path never returns None

    return {
        "queue_ids": queue_ids,
        "by_queue_and_contest": agg.by_queue_and_contest,
        "by_contest_total": agg.by_contest_total,
        "total_items": agg.total_items,
        "unknown_contest_items": agg.unknown_contest_items,
        "missing_ratio": agg.missing_ratio,
        "metrics_latency_ms": agg.metrics_latency_ms,
    }


def render_queue_metrics(metrics: dict[str, Any]) -> None:
    queue_ids = metrics["queue_ids"]
    by_queue_and_contest = metrics["by_queue_and_contest"]
    by_contest_total = metrics["by_contest_total"]
    total_items = metrics["total_items"]
    unknown_contest_items = metrics["unknown_contest_items"]
    missing_ratio = metrics["missing_ratio"]
    metrics_latency_ms = metrics["metrics_latency_ms"]

    print("=== Queues ===")
    for queue_key in QUEUE_KEYS:
        ids = queue_ids[queue_key]
        print(f"  {queue_key}: {len(ids)} item(s)")
        if ids:
            for item in ids:
                print(f"    - {item}")

    print("\n=== Per-contest queue metrics ===")
    for queue_key in QUEUE_KEYS:
        queue_counts = by_queue_and_contest[queue_key]
        print(f"  {queue_key}")
        if not queue_counts:
            print("    (empty)")
            continue
        for contest_id, count in queue_counts.items():
            print(f"    {contest_id}: {count}")

    print("\n=== Per-contest totals (all queues) ===")
    if by_contest_total:
        for contest_id, count in by_contest_total.items():
            print(f"  {contest_id}: {count}")
    else:
        print("  (empty)")

    print("\n=== Observability ===")
    print(f"  total_queue_items: {total_items}")
    print(f"  unknown_contest_items: {unknown_contest_items}")
    print(f"  missing_contest_ratio: {missing_ratio:.4f}")
    print(f"  metrics_latency_ms: {metrics_latency_ms:.2f}")


async def clear_all(client: aivalkey.Valkey) -> None:
    job_keys = await _scan_job_hash_keys(client)
    keys_to_delete = [
        *QUEUE_KEYS,
        settings.queue_inflight_times_key,
        *job_keys,
    ]
    if not keys_to_delete:
        print("Nothing to clear.")
        return
    deleted = await client.delete(*keys_to_delete)
    print(f"Deleted {deleted} key(s).")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Valkey queue status / maintenance tool")
    parser.add_argument("--clear", action="store_true", help="Delete all queues and job hashes")
    args = parser.parse_args()

    client = aivalkey.Valkey.from_url(settings.valkey_url, decode_responses=True)
    try:
        await client.ping()
        print(f"Connected to {settings.valkey_url}\n")

        if args.clear:
            await clear_all(client)
            return

        metrics = await collect_queue_metrics(client)
        render_queue_metrics(metrics)

        # Inflight times sorted set
        inflight_times = await client.zrange(settings.queue_inflight_times_key, 0, -1, withscores=True)
        print(f"\n=== {settings.queue_inflight_times_key} ===")
        if inflight_times:
            for judgment_id, score in inflight_times:
                print(f"  {judgment_id}  (score={score})")
        else:
            print("  (empty)")

        # Job hashes
        job_keys = await _scan_job_hash_keys(client)
        print(f"\n=== Job hashes ({len(job_keys)} total) ===")
        sorted_job_keys = sorted(job_keys)
        pipe = client.pipeline()
        for job_key in sorted_job_keys:
            pipe.hgetall(job_key)
        job_hashes = await pipe.execute()

        for job_key, fields in zip(sorted_job_keys, job_hashes, strict=True):
            print(f"  {job_key}")
            for field, value in (fields or {}).items():
                print(f"    {field}: {value}")

    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
