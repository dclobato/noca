#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Bootstrap script — creates the initial UberAdmin user.

Usage:
    uv run python scripts/web/create_uberadmin.py
    uv run python scripts/web/create_uberadmin.py --username admin --email admin@example.com

Idempotent: if a user with the given username already exists, the script exits cleanly.
"""

import argparse
import asyncio
import logging
import sys

from sqlalchemy import select
from sqlalchemy.pool import NullPool

from shared.app_logging import configure_logging
from web.config import settings
from web.database import create_engine, create_session_factory
from web.models.users import UberAdmin

logger = logging.getLogger(__name__)

DEFAULT_USERNAME = settings.UBERADMIN_USERNAME or "uberadmin"
DEFAULT_FULLNAME = settings.UBERADMIN_FULLNAME or "Uber Administrator"
DEFAULT_EMAIL = settings.UBERADMIN_EMAIL or "uberadmin@example.com"
DEFAULT_PASSWORD = settings.UBERADMIN_PASSWORD or "StrongPasswd1!"


async def create_uberadmin(
    username: str,
    fullname: str,
    email: str,
    password: str,
) -> None:
    engine = create_engine(poolclass=NullPool)
    session_factory = create_session_factory(engine)

    try:
        async with session_factory() as session:
            existing = await session.scalar(select(UberAdmin).where(UberAdmin.username == username))
            if existing:
                logger.warning("UberAdmin '%s' already exists — nothing to do.", username)
                return

            user = UberAdmin()
            user.username = username
            user.fullname = fullname
            user.email = email  # uses the setter → normalises + validates
            user.password = password  # uses the setter → hashes with werkzeug

            session.add(user)
            await session.commit()
            logger.info("UberAdmin '%s' created successfully.", username)
    finally:
        await engine.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create the initial UberAdmin user.")
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--fullname", default=DEFAULT_FULLNAME)
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    return parser.parse_args()


if __name__ == "__main__":
    configure_logging()
    args = parse_args()
    asyncio.run(
        create_uberadmin(
            username=args.username,
            fullname=args.fullname,
            email=args.email,
            password=args.password,
        )
    )
    sys.exit(0)
