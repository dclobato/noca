#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Bounded record of the reveal commands a ceremony has already applied.

An operator command can succeed durably and still reach its caller as an
*unknown* outcome: the process can die after the fenced save, the connection can
drop, a proxy can time out. Without a record of what was applied, the only safe
response to that ambiguity is "never retry" — which is why the operator panel
locks itself. A receipt makes the honest answer possible instead: a retry
carrying the same key returns the **original** result rather than advancing the
ceremony a second time in front of an audience.

Two properties make these receipts trustworthy:

- **They are part of the session state**, not a sibling key. The store's fenced
  save writes state and receipts in one atomic Lua write under the scope lock, so
  "was this command applied?" and "what is the state?" can never disagree, and a
  receipt cannot outlive the ceremony it describes.
- **They carry nothing sensitive.** A receipt is a caller-chosen key plus the
  command name. Operator tokens, digests, and scopes are never recorded here.

The ring is bounded twice over: by :data:`MAX_COMMAND_RECEIPTS` entries and by
the state key's own TTL, since it is stored inside the state. The count bound is
the tighter of the two and is deliberate — retries happen within seconds of the
command they repeat, and an unbounded ledger would grow for the length of a
ceremony to answer a question nobody asks anymore.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from shared.reveal_schema import RevealCommand

__all__ = [
    "IDEMPOTENCY_KEY_PATTERN",
    "MAX_COMMAND_RECEIPTS",
    "CommandReceipt",
    "append_receipt",
]

MAX_COMMAND_RECEIPTS: Final = 8
"""How many applied commands a ceremony remembers, newest last.

Deep enough that an operator's retry — which follows its command by seconds —
always finds its receipt, shallow enough that the persisted state stays small.
Beyond this depth a repeated key is reported as *superseded* rather than
replayed, which is a stated refusal the operator can act on, never a silent
second application.
"""

IDEMPOTENCY_KEY_PATTERN: Final = r"^[A-Za-z0-9_-]{8,128}$"
"""Accepted shape of a caller-supplied idempotency key.

Deliberately the same character class the Valkey key/channel guard uses, and
long enough to make an accidental collision between two operators implausible. A
key that does not match is refused with ``422`` before it can reach the store, so
a malformed key can never be recorded as if it identified a command.
"""


class CommandReceipt(BaseModel):
    """One applied command, identified by its caller-supplied key.

    Attributes:
        key: The ``Idempotency-Key`` the caller sent with the command.
        command: The command that key applied. Stored so a key reused for a
            *different* command is refused instead of replaying the wrong result.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=8, max_length=128, pattern=IDEMPOTENCY_KEY_PATTERN)
    command: RevealCommand


def append_receipt(ring: Sequence[CommandReceipt], receipt: CommandReceipt) -> tuple[CommandReceipt, ...]:
    """Return ``ring`` with ``receipt`` appended, trimmed to the ring bound.

    Trimming drops the *oldest* entries, so the newest command — the only one a
    retry can replay — is never the one evicted.

    Args:
        ring: The current receipts, oldest first.
        receipt: The receipt for the command just applied.

    Returns:
        The new ring, at most :data:`MAX_COMMAND_RECEIPTS` long.
    """
    return (*ring, receipt)[-MAX_COMMAND_RECEIPTS:]
