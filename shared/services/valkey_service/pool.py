#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Valkey pool creation helpers."""

from __future__ import annotations

import valkey.asyncio as aivalkey
from valkey.asyncio.connection import ConnectionPool


def create_valkey_pool(valkey_url: str) -> ConnectionPool:
    """Create an async Valkey connection pool from a URL."""
    return aivalkey.ConnectionPool.from_url(
        valkey_url,
        decode_responses=True,
    )
