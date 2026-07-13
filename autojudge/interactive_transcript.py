#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Ordered line-by-line recording of an interactive validator conversation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from autojudge.runtime_utils import decode_for_text_column

TRANSCRIPT_MAX_BYTES = 256 * 1024

TranscriptDirection = Literal["user", "validator"]


@dataclass(frozen=True)
class TranscriptLine:
    """One protocol line, attributed to the side that produced it."""

    direction: TranscriptDirection
    line: str
    partial: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the ``transcript`` JSON column."""
        entry: dict[str, Any] = {"dir": self.direction, "line": self.line}
        if self.partial:
            entry["partial"] = True
        return entry


@dataclass(frozen=True)
class InteractiveTranscript:
    """The full ordered conversation captured for one interactive attempt."""

    lines: list[TranscriptLine]
    truncated: bool

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the ``transcript`` JSON column."""
        return {"lines": [line.as_dict() for line in self.lines], "truncated": self.truncated}


@dataclass
class TranscriptRecorder:
    """Build one ordered transcript from both sides of a relayed conversation.

    The bridge relays every byte between the two processes, so feeding each
    relayed chunk here records the conversation in the order the judge observed
    it. Recording is capture-only: it never raises and never blocks the relay,
    so a verdict can never depend on it. Once ``max_bytes`` of line text has been
    recorded the transcript stops growing and reports itself truncated, while the
    bridge keeps pumping bytes.
    """

    max_bytes: int = TRANSCRIPT_MAX_BYTES
    _lines: list[TranscriptLine] = field(default_factory=list)
    _buffers: dict[str, bytearray] = field(default_factory=dict)
    _recorded_bytes: int = 0
    _truncated: bool = False

    def record(self, direction: TranscriptDirection, chunk: bytes) -> None:
        """Record complete lines from one relayed chunk.

        Chunk boundaries are not line boundaries: a single read may carry many
        lines, or half of one. Partial lines stay buffered until their newline
        arrives, so entries always correspond to protocol lines.
        """
        buffer = self._buffers.setdefault(direction, bytearray())
        buffer.extend(chunk)
        while (newline_index := buffer.find(b"\n")) != -1:
            raw_line = bytes(buffer[:newline_index])
            del buffer[: newline_index + 1]
            self._append(direction, raw_line, partial=False)

    def close(self, direction: TranscriptDirection) -> None:
        """Flush a trailing line that ended without a newline (EOF)."""
        buffer = self._buffers.get(direction)
        if buffer:
            raw_line = bytes(buffer)
            buffer.clear()
            self._append(direction, raw_line, partial=True)

    def build(self) -> InteractiveTranscript:
        """Return the recorded conversation, flushing any buffered remainders."""
        for direction in tuple(self._buffers):
            self.close(direction)  # type: ignore[arg-type]
        return InteractiveTranscript(lines=list(self._lines), truncated=self._truncated)

    def _append(self, direction: TranscriptDirection, raw_line: bytes, *, partial: bool) -> None:
        """Append one decoded line unless the capture cap has been reached."""
        if self._truncated:
            return
        if self._recorded_bytes + len(raw_line) > self.max_bytes:
            self._truncated = True
            return
        self._recorded_bytes += len(raw_line)
        line = decode_for_text_column(raw_line.removesuffix(b"\r"))
        self._lines.append(TranscriptLine(direction=direction, line=line, partial=partial))
