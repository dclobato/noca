#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Valkey value decoding helpers.

The worker connects to Valkey with ``decode_responses=False``, so list items
and hash fields come back as ``bytes``. These helpers normalize those raw values
into the text/int shapes the queue reconciliation and dispatch code expects.

Public API
----------
- decode_valkey_scalar(value): bytes/str/None -> str | None
- hash_requeue_count(hash_data): job hash -> requeue_count int (defaults to 0)
"""

from contextlib import suppress
from typing import Any


def decode_valkey_scalar(value: Any) -> str | None:
    """Decode a Valkey scalar into text when present.

    Args:
        value: Raw value from Valkey (``bytes``, ``str``, ``None``, or other).

    Returns:
        The decoded text, or ``None`` when the input is ``None``.
    """
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def hash_requeue_count(hash_data: dict[Any, Any]) -> int:
    """Extract ``requeue_count`` from a Valkey job hash, defaulting to zero.

    Tolerates both ``bytes``- and ``str``-keyed hashes so it works regardless of
    the client's ``decode_responses`` setting.

    Args:
        hash_data: The job hash as returned by ``HGETALL`` (possibly empty).

    Returns:
        The parsed ``requeue_count``, or ``0`` when absent or unparseable.
    """
    if not hash_data:
        return 0

    if isinstance(next(iter(hash_data.keys())), bytes):
        raw_count = hash_data.get(b"requeue_count")
    else:
        raw_count = hash_data.get("requeue_count")

    decoded_count = decode_valkey_scalar(raw_count)
    if decoded_count is None:
        return 0

    with suppress(ValueError):
        return int(decoded_count)
    return 0
