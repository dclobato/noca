#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Backfill missing `contest_id` on `judge:job:*` Valkey hashes.

This script is intended for transitional rollout when older job hashes were
written without `contest_id`. It scans Valkey job keys, resolves each missing
judgment via PostgreSQL once, and writes `contest_id` back into the hash.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from collections.abc import Sequence

import valkey.asyncio as aivalkey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from shared.db_schema import problems, submission_judgments, submissions
from web.config import settings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill contest_id in Valkey judge job hashes")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="SCAN count hint and processing batch size (default: 500)",
    )
    parser.add_argument(
        "--write-sleep-ms",
        type=int,
        default=0,
        help="Sleep this many milliseconds after each write batch (default: 0)",
    )
    parser.add_argument(
        "--max-updates",
        type=int,
        default=None,
        help="Optional safety cap for number of hashes to update",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report planned updates without writing")
    return parser


def _job_key(judgment_id: str) -> str:
    return f"{settings.queue_job_hash_prefix}:{judgment_id}"


def _judgment_id_from_job_key(key: str) -> str | None:
    prefix = f"{settings.queue_job_hash_prefix}:"
    if not key.startswith(prefix):
        return None
    judgment_id = key[len(prefix) :].strip()
    return judgment_id or None


def _chunked(items: Sequence[str], size: int) -> list[list[str]]:
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


async def _scan_job_keys(client: aivalkey.Valkey, *, batch_size: int) -> list[str]:
    keys: list[str] = []
    cursor = 0
    pattern = f"{settings.queue_job_hash_prefix}:*"
    while True:
        cursor, batch = await client.scan(cursor=cursor, match=pattern, count=batch_size)
        keys.extend(batch)
        if cursor == 0:
            break
    return keys


async def _resolve_contest_rows(
    session_factory: async_sessionmaker,
    judgment_ids: list[str],
) -> dict[str, tuple[str, str]]:
    if not judgment_ids:
        return {}

    stmt = (
        select(
            submission_judgments.c.id.label("judgment_id"),
            submissions.c.id.label("submission_id"),
            problems.c.contest_id.label("contest_id"),
        )
        .select_from(
            submission_judgments.join(
                submissions,
                submissions.c.id == submission_judgments.c.submission_id,
            ).join(
                problems,
                problems.c.id == submissions.c.problem_id,
            )
        )
        .where(submission_judgments.c.id.in_(judgment_ids))
    )

    async with session_factory() as session:
        rows = (await session.execute(stmt)).mappings().all()

    return {str(row["judgment_id"]): (str(row["contest_id"]), str(row["submission_id"])) for row in rows}


async def run_backfill(args: argparse.Namespace) -> int:
    if args.batch_size <= 0:
        raise ValueError("--batch-size must be > 0")
    if args.write_sleep_ms < 0:
        raise ValueError("--write-sleep-ms must be >= 0")
    if args.max_updates is not None and args.max_updates <= 0:
        raise ValueError("--max-updates must be > 0")

    valkey = aivalkey.Valkey.from_url(settings.valkey_url, decode_responses=True)
    engine = create_async_engine(settings.db_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    started = time.perf_counter()
    scanned = 0
    missing_contest = 0
    updated = 0
    unresolved = 0

    try:
        await valkey.ping()
        keys = await _scan_job_keys(valkey, batch_size=args.batch_size)

        for key_batch in _chunked(keys, args.batch_size):
            scanned += len(key_batch)

            read_pipe = valkey.pipeline()
            for key in key_batch:
                read_pipe.hgetall(key)
            raw_hashes = await read_pipe.execute()

            missing_batch: list[tuple[str, str, dict[str, str]]] = []
            for key, job_hash in zip(key_batch, raw_hashes, strict=True):
                judgment_id = _judgment_id_from_job_key(key)
                if judgment_id is None:
                    continue
                contest_id = (job_hash or {}).get("contest_id", "").strip()
                if contest_id:
                    continue
                missing_contest += 1
                missing_batch.append((key, judgment_id, job_hash or {}))

            if not missing_batch:
                continue

            resolved = await _resolve_contest_rows(
                session_factory,
                [judgment_id for _, judgment_id, _ in missing_batch],
            )

            write_pipe = valkey.pipeline()
            writes_in_batch = 0

            for key, judgment_id, existing_hash in missing_batch:
                if args.max_updates is not None and updated >= args.max_updates:
                    break

                resolved_row = resolved.get(judgment_id)
                if resolved_row is None:
                    unresolved += 1
                    continue

                contest_id, submission_id = resolved_row
                if args.dry_run:
                    updated += 1
                    continue

                mapping = {"contest_id": contest_id}
                if not existing_hash.get("submission_id", "").strip():
                    mapping["submission_id"] = submission_id

                write_pipe.hset(key, mapping=mapping)
                writes_in_batch += 1
                updated += 1

            if writes_in_batch > 0:
                await write_pipe.execute()
                if args.write_sleep_ms > 0:
                    await asyncio.sleep(args.write_sleep_ms / 1000.0)

            if args.max_updates is not None and updated >= args.max_updates:
                break

    finally:
        await valkey.aclose()
        await engine.dispose()

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    print("Backfill summary")
    print(f"  dry_run: {bool(args.dry_run)}")
    print(f"  scanned: {scanned}")
    print(f"  missing_contest_id: {missing_contest}")
    print(f"  updated: {updated}")
    print(f"  unresolved: {unresolved}")
    print(f"  duration_ms: {elapsed_ms:.2f}")

    return 0


async def _main() -> int:
    args = _build_parser().parse_args()
    return await run_backfill(args)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
