#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Create one durable Arena notification for a target user.

Usage:
    uv run python scripts/arena/smoke_test_arena_notification.py --user-id <arena-user-id>
    uv run python scripts/arena/smoke_test_arena_notification.py \\
        --user-id <arena-user-id> \\
        --title "Smoke notification" \\
        --message "This is a test notification from the smoke script."
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

from sqlalchemy.pool import NullPool

sys.path.insert(0, str(Path(__file__).parents[2]))

import arena.models  # noqa: E402, F401
from arena.config import settings as arena_settings  # noqa: E402
from arena.database import create_engine, create_session_factory  # noqa: E402
from arena.models.arena_users import ArenaUser  # noqa: E402
from shared.enumerations import ArenaNotificationKind  # noqa: E402
from shared.services.arena_notification_service import create_arena_notification  # noqa: E402

DEFAULT_TITLE = "Smoke notification"
DEFAULT_MESSAGE = "This is a test notification from the Arena notification smoke script."


def _build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Create one test Arena notification for an existing Arena user.",
    )
    parser.add_argument("--user-id", required=True, help="Arena user UUID that will receive the notification.")
    parser.add_argument("--title", default=DEFAULT_TITLE, help=f"Notification title. Default: {DEFAULT_TITLE!r}.")
    parser.add_argument(
        "--message",
        default=DEFAULT_MESSAGE,
        help=f"Notification message. Default: {DEFAULT_MESSAGE!r}.",
    )
    parser.add_argument(
        "--target-url",
        default=None,
        help="Optional target URL. Omit to keep target_url NULL, matching v1 notification behavior.",
    )
    return parser


async def _run() -> int:
    """Create and commit one notification row for the requested Arena user."""
    args = _build_parser().parse_args()
    engine = create_engine(arena_settings.db_url, poolclass=NullPool)
    session_factory = create_session_factory(engine)

    try:
        async with session_factory() as session:
            user = await session.get(ArenaUser, args.user_id)
            if user is None:
                print(f"ERROR: Arena user not found: {args.user_id}", file=sys.stderr)
                return 1

            source_ref = f"smoke:{uuid.uuid4()}"
            notification_id = await create_arena_notification(
                session,
                user_id=user.id,
                notification_kind=ArenaNotificationKind.OTHER,
                title=args.title,
                message=args.message,
                target_url=args.target_url,
                source_ref=source_ref,
                context={"source": "smoke_test_arena_notification"},
            )
            await session.commit()

            print("SUCCESS: Arena notification created")
            print(f"  notification_id: {notification_id}")
            print(f"  user_id:         {user.id}")
            print(f"  user_email:      {user.email_normalizado}")
            print(f"  kind:            {ArenaNotificationKind.AI_REVIEW_COMPLETED.value}")
            print(f"  source_ref:      {source_ref}")
            print(f"  target_url:      {args.target_url or 'NULL'}")
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
