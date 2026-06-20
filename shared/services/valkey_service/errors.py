#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared Valkey error helpers."""

from __future__ import annotations

from valkey.exceptions import ConnectionError as ValkeyConnectionError
from valkey.exceptions import TimeoutError as ValkeyTimeoutError


def is_recoverable_valkey_error(exc: BaseException) -> bool:
    """Return whether *exc* should trigger reconnect/best-effort handling."""
    return isinstance(exc, (ValkeyConnectionError, ValkeyTimeoutError, OSError))
