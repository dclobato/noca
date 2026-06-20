#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Pure status-mapping and response-parsing helpers for the batch poller.

This module is intentionally free of I/O: it only translates OpenAI batch
statuses to the local ``ArenaAIBatchJobStatus`` and extracts text/error
messages from already-parsed OpenAI response structures.
"""

from __future__ import annotations

from shared.enumerations import ArenaAIBatchJobStatus

# OpenAI batch statuses that mean "done, one way or another"
OPENAI_TERMINAL_STATUSES = frozenset({"completed", "failed", "expired", "cancelled"})
# OpenAI treats "cancelling" as a transient state that resolves to "cancelled"
OPENAI_FAILED_STATUSES = frozenset({"failed", "expired", "cancelled", "cancelling"})

_LOCAL_STATUS_MAP = {
    "completed": ArenaAIBatchJobStatus.COMPLETED.value,
    "failed": ArenaAIBatchJobStatus.FAILED.value,
    "expired": ArenaAIBatchJobStatus.EXPIRED.value,
    "cancelled": ArenaAIBatchJobStatus.CANCELLED.value,
    "cancelling": ArenaAIBatchJobStatus.CANCELLED.value,
}


def is_terminal_status(openai_status: str) -> bool:
    """Return True when the OpenAI status is terminal (incl. ``cancelling``).

    ``cancelling`` is treated as terminal because it deterministically resolves
    to ``cancelled``; processing it immediately avoids an extra poll cycle.

    Args:
        openai_status: Raw OpenAI batch status string.

    Returns:
        True when the status should trigger finalization.
    """
    return openai_status in OPENAI_TERMINAL_STATUSES or openai_status == "cancelling"


def openai_status_to_local(openai_status: str) -> str:
    """Map an OpenAI terminal batch status to the local ``ArenaAIBatchJobStatus``.

    Args:
        openai_status: Raw OpenAI status string.

    Returns:
        Corresponding ``ArenaAIBatchJobStatus`` value string. Unknown statuses
        fall back to ``FAILED`` to avoid silent data loss.
    """
    return _LOCAL_STATUS_MAP.get(openai_status, ArenaAIBatchJobStatus.FAILED.value)


def extract_output_text(body: dict) -> str | None:  # type: ignore[type-arg]
    """Extract the output text from a Responses API response body.

    Traverses ``body.output[*].content[*]`` looking for an item whose
    ``type`` is ``"message"`` at the output level and ``"output_text"`` at
    the content level.

    Args:
        body: Parsed ``response.body`` dict from the batch output JSONL.

    Returns:
        The text string, or ``None`` when not found.
    """
    for output_item in body.get("output") or []:
        if output_item.get("type") != "message":
            continue
        for content_item in output_item.get("content") or []:
            if content_item.get("type") == "output_text":
                text = content_item.get("text")
                if text:
                    return str(text)
    return None


def extract_batch_error(batch: object) -> str | None:
    """Extract a human-readable error message from an OpenAI batch object.

    Args:
        batch: OpenAI batch object.

    Returns:
        Error message string, or ``None`` when not available.
    """
    errors = getattr(batch, "errors", None)
    if errors is None:
        return None
    data = getattr(errors, "data", None) or []
    if data:
        first = data[0]
        msg = getattr(first, "message", None)
        return str(msg) if msg else None
    return None
