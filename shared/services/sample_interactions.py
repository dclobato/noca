#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Domain-neutral parsing and presentation of interactive sample interactions.

A problem with a custom validator cannot show sample test cases: what a
contestant needs to see is the *conversation* their program will have with the
validator. Authors therefore write sample interactions as plain text::

    > 3
    < 5
    > !8

``> `` is a line the validator sends to the contestant; ``< `` is a line the
contestant sends back. The text is parsed into the same transcript JSON the judge
records for real interactive attempts
(``submission_interactive_attempts.transcript``), so one renderer serves both.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

MAX_SAMPLE_INTERACTIONS = 5
_VALIDATOR_PREFIX = "> "
_USER_PREFIX = "< "
_INTERACTION_FILE_RE = re.compile(r"^interaction/0*([1-9]\d{0,2})\.interaction$", re.IGNORECASE)
_EXPLAIN_FILE_RE = re.compile(r"^interaction/0*([1-9]\d{0,2})\.explain$", re.IGNORECASE)


class InteractionParseError(ValueError):
    """Raised when authored or packaged interaction content is malformed."""


@dataclass(frozen=True)
class PackagedInteraction:
    """One sample interaction read from a problem package."""

    transcript: dict[str, object]
    explanation: str | None


@dataclass(frozen=True)
class SampleInteractionRowView:
    """One sample-interaction row for the shared admin list table.

    Attributes:
        id: Interaction identifier (DOM ids and pending-removal tracking).
        ordinal: 1-based display position.
        preview: Already-truncated preview of the transcript's opening lines.
        line_count: Number of conversation lines in the transcript.
        has_explanation: Whether an author explanation is present.
        edit_url: Per-row edit URL.
        move_url: Per-row reorder POST URL.
    """

    id: str
    ordinal: int
    preview: str
    line_count: int
    has_explanation: bool
    edit_url: str
    move_url: str


def parse_interaction_text(text: str) -> dict[str, object]:
    """Parse an authored plain-text interaction into transcript JSON.

    Every line must begin with the exact two-character prefix ``"> "`` (validator
    to contestant) or ``"< "`` (contestant to validator). Everything after the
    prefix is the protocol line, preserved verbatim — trailing spaces included, as
    they are part of what the program actually wrote. A bare ``"> "`` therefore
    represents an empty protocol line. Bare markers without the trailing space,
    leading whitespace, and blank lines are all rejected.

    A single trailing newline is tolerated, since that is what a textarea normally
    submits. Any blank line beyond it is a malformed line like any other.

    Args:
        text: The raw textarea content.

    Returns:
        A transcript mapping ``{"lines": [...], "truncated": False}``.

    Raises:
        InteractionParseError: If the text is empty or any line is malformed.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    # Drop exactly one trailing newline (the textarea's own), not every blank line:
    # a blank line the author actually typed must still be reported as malformed.
    if normalized.endswith("\n"):
        normalized = normalized[:-1]
    raw_lines = normalized.split("\n")

    if not raw_lines or raw_lines == [""]:
        raise InteractionParseError("A sample interaction must have at least one line.")

    lines: list[dict[str, object]] = []
    for number, raw in enumerate(raw_lines, start=1):
        if raw.startswith(_VALIDATOR_PREFIX):
            lines.append({"dir": "validator", "line": raw[len(_VALIDATOR_PREFIX) :]})
        elif raw.startswith(_USER_PREFIX):
            lines.append({"dir": "user", "line": raw[len(_USER_PREFIX) :]})
        else:
            raise InteractionParseError(
                f"Line {number}: every line must start with '> ' (validator output) "
                f"or '< ' (your program's output), including the space."
            )
    return {"lines": lines, "truncated": False}


def transcript_to_text(transcript: dict[str, object]) -> str:
    """Render a transcript back into the authoring plain-text format."""
    lines = transcript.get("lines")
    if not isinstance(lines, list):
        return ""
    rendered: list[str] = []
    for entry in lines:
        if not isinstance(entry, dict):
            continue
        prefix = _USER_PREFIX if entry.get("dir") == "user" else _VALIDATOR_PREFIX
        rendered.append(f"{prefix}{entry.get('line', '')}")
    return "\n".join(rendered)


def transcript_preview(transcript: dict[str, object], *, limit: int = 60, max_lines: int = 10) -> str:
    """Return a preview of a transcript's opening lines, in authoring format.

    Each line keeps its ``> ``/``< `` marker and is truncated to ``limit`` characters
    so no single line can blow out the admin list column. A conversation longer than
    ``max_lines`` ends with an ellipsis line marking the lines that are not shown.

    Args:
        transcript: The transcript mapping.
        limit: Maximum characters per previewed line, ellipsis included.
        max_lines: Maximum conversation lines to preview.

    Returns:
        The newline-joined preview, empty if the transcript holds no usable lines.
    """
    lines = transcript.get("lines")
    if not isinstance(lines, list) or not lines:
        return ""
    previewed: list[str] = []
    for entry in lines[:max_lines]:
        if not isinstance(entry, dict):
            continue
        prefix = _USER_PREFIX if entry.get("dir") == "user" else _VALIDATOR_PREFIX
        text = f"{prefix}{entry.get('line', '')}"
        previewed.append(text if len(text) <= limit else f"{text[: limit - 1]}…")
    if not previewed:
        return ""
    if len(lines) > max_lines:
        previewed.append("…")
    return "\n".join(previewed)


def transcript_line_count(transcript: dict[str, object]) -> int:
    """Return the number of conversation lines in a transcript."""
    lines = transcript.get("lines")
    return len(lines) if isinstance(lines, list) else 0


def validate_transcript_json(obj: object) -> dict[str, object]:
    """Validate a decoded transcript structure read from a package.

    Args:
        obj: The decoded JSON value.

    Returns:
        The validated transcript mapping.

    Raises:
        InteractionParseError: If the structure violates the transcript contract.
    """
    if not isinstance(obj, dict):
        raise InteractionParseError("A transcript must be a JSON object.")
    lines = obj.get("lines")
    if not isinstance(lines, list) or not lines:
        raise InteractionParseError("A transcript must have a non-empty 'lines' array.")
    validated: list[dict[str, object]] = []
    for index, entry in enumerate(lines, start=1):
        if not isinstance(entry, dict):
            raise InteractionParseError(f"Transcript line {index} must be an object.")
        direction = entry.get("dir")
        line = entry.get("line")
        if direction not in ("user", "validator"):
            raise InteractionParseError(f"Transcript line {index}: 'dir' must be 'user' or 'validator'.")
        if not isinstance(line, str):
            raise InteractionParseError(f"Transcript line {index}: 'line' must be a string.")
        validated.append({"dir": direction, "line": line})
    truncated = obj.get("truncated", False)
    if not isinstance(truncated, bool):
        raise InteractionParseError("Transcript 'truncated' must be a boolean.")
    return {"lines": validated, "truncated": truncated}


def parse_packaged_interactions(
    *,
    archive_names: set[str],
    read_file: Callable[[str], bytes],
) -> list[PackagedInteraction]:
    """Read ``interaction/NNN.interaction`` and ``interaction/NNN.explain`` members.

    Ordinals are remapped contiguously from 1 in sorted order, matching how
    :func:`shared.tc_zip.parse_testcases_zip` treats test cases. An ``.explain``
    file with no matching ``.interaction`` file is ignored.

    Args:
        archive_names: Exact archive member names.
        read_file: Callable returning the bytes of an archive member.

    Returns:
        The packaged interactions in ordinal order.

    Raises:
        InteractionParseError: If a member is not valid UTF-8 JSON, violates the
            transcript contract, or the package holds more than the allowed number
            of interactions.
    """
    transcripts: dict[int, str] = {}
    explanations: dict[int, str] = {}
    for name in archive_names:
        match = _INTERACTION_FILE_RE.match(name)
        if match:
            transcripts[int(match.group(1))] = name
            continue
        explain_match = _EXPLAIN_FILE_RE.match(name)
        if explain_match:
            explanations[int(explain_match.group(1))] = name

    if len(transcripts) > MAX_SAMPLE_INTERACTIONS:
        raise InteractionParseError(
            f"A problem may have at most {MAX_SAMPLE_INTERACTIONS} sample interactions; "
            f"the package has {len(transcripts)}."
        )

    parsed: list[PackagedInteraction] = []
    for ordinal in sorted(transcripts):
        raw = read_file(transcripts[ordinal])
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise InteractionParseError(f"{transcripts[ordinal]} must be valid UTF-8.") from exc
        except json.JSONDecodeError as exc:
            raise InteractionParseError(f"{transcripts[ordinal]} must contain a valid JSON transcript.") from exc
        transcript = validate_transcript_json(decoded)
        parsed.append(PackagedInteraction(transcript, _read_explanation(explanations.get(ordinal), read_file)))
    return parsed


def build_interaction_files(
    interactions: list[tuple[dict[str, object], str | None]],
) -> list[tuple[str, bytes]]:
    """Build the ``interaction/`` archive members for a problem export.

    Args:
        interactions: Transcript/explanation pairs already in display order.

    Returns:
        ``(archive_name, content)`` pairs, numbered sequentially from ``001``.
    """
    members: list[tuple[str, bytes]] = []
    for position, (transcript, explanation) in enumerate(interactions, start=1):
        members.append(
            (f"interaction/{position:03d}.interaction", json.dumps(transcript, ensure_ascii=False).encode("utf-8"))
        )
        if explanation:
            members.append((f"interaction/{position:03d}.explain", explanation.encode("utf-8")))
    return members


def interactive_testcase_violation(*, total_cases: int, sample_cases: int) -> str | None:
    """Return why an interactive problem's test cases are invalid, or ``None``.

    A problem with a configured custom validator presents sample interactions
    instead of sample test cases, so it must have no public cases at all and at
    least one secret case to judge against.
    """
    if sample_cases > 0:
        return "Interactive problems present sample interactions instead of sample test cases."
    if total_cases < 1:
        return "An interactive problem needs at least one secret test case."
    return None


def _read_explanation(name: str | None, read_file: Callable[[str], bytes]) -> str | None:
    """Decode an optional explanation member, or return ``None``."""
    if name is None:
        return None
    try:
        text = read_file(name).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InteractionParseError(f"{name} must be valid UTF-8.") from exc
    return text.strip() or None
