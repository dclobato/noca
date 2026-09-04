#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Pattern-based key discovery and counted deletion on a raw Valkey client.

``SCAN`` walks the whole keyspace, so these helpers are for rare
administrative operations (an operator lifting a lockout), never for a
request hot path. They sit beside :mod:`contest_purge`, the other place that
scans, and like it they take the raw client so the runtime wrapper owns
availability bookkeeping while this module owns only the protocol.
"""

from __future__ import annotations

from collections.abc import Sequence

import valkey.asyncio as aivalkey

__all__ = ["delete_keys_with_client", "scan_keys_with_client"]

_SCAN_COUNT_HINT = 500
_DELETE_CHUNK_SIZE = 500


async def scan_keys_with_client(client: aivalkey.Valkey, pattern: str) -> list[str]:
    """Return every key matching a ``SCAN`` glob pattern.

    Args:
        client: Raw async Valkey client.
        pattern: ``MATCH`` glob (``*`` and ``?`` are wildcards).

    Returns:
        The matching key names, decoded, in scan order. ``SCAN`` may report a
        key more than once across cursor pages, so callers that count should
        deduplicate.
    """
    keys: list[str] = []
    async for raw_key in client.scan_iter(match=pattern, count=_SCAN_COUNT_HINT):
        keys.append(raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key))
    return keys


async def delete_keys_with_client(client: aivalkey.Valkey, keys: Sequence[str]) -> int:
    """Delete ``keys`` in bounded chunks and return how many existed.

    Args:
        client: Raw async Valkey client.
        keys: Key names to delete; an empty sequence deletes nothing.

    Returns:
        The number of keys the server actually removed, summed over chunks.
    """
    removed = 0
    for start in range(0, len(keys), _DELETE_CHUNK_SIZE):
        chunk = keys[start : start + _DELETE_CHUNK_SIZE]
        removed += int(await client.delete(*chunk))
    return removed
