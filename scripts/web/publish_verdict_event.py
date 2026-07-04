#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import argparse
import asyncio
import uuid

from shared.queue_schema import VerdictEvent
from web.config import settings
from web.services.valkey_service import ValkeyRuntime


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish a synthetic verdict event to test runs SSE refresh.",
    )
    parser.add_argument("--contest-id", required=True, help="Contest UUID that should receive the SSE update.")
    parser.add_argument("--verdict", default="AC", help="Verdict label to publish. Default: AC.")
    parser.add_argument(
        "--submission-id",
        default="",
        help="Optional submission UUID. A random UUID is generated when omitted.",
    )
    parser.add_argument(
        "--judgment-id",
        default="",
        help="Optional judgment UUID. A random UUID is generated when omitted.",
    )
    return parser


async def _run() -> None:
    args = _build_parser().parse_args()
    runtime = ValkeyRuntime(
        valkey_url=settings.valkey_url,
        healthcheck_interval_s=settings.VALKEY_HEALTHCHECK_INTERVAL_SECONDS,
    )
    await runtime.start()
    try:
        event = VerdictEvent(
            submission_id=args.submission_id or str(uuid.uuid4()),
            judgment_id=args.judgment_id or str(uuid.uuid4()),
            verdict=args.verdict,
            contest_id=args.contest_id,
        )
        await runtime.publish_verdict(event)
        print(
            "Published verdict event:",
            f"contest_id={event.contest_id}",
            f"submission_id={event.submission_id}",
            f"judgment_id={event.judgment_id}",
            f"verdict={event.verdict}",
        )
    finally:
        await runtime.stop()


if __name__ == "__main__":
    asyncio.run(_run())
