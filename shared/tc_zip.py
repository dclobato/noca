#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared utility for parsing test-case ZIP archives.

Supports two ZIP layouts:

* **Directory layout** — ``in/001.in`` / ``out/001.out`` (or ``out/001.sol``)
* **Flat layout** — ``001.in`` / ``001.out`` (or ``001.sol``)

Leading zeros in filenames are stripped so ``001.in`` and ``1.in`` refer to the
same ordinal.  Ordinals are remapped contiguously starting at 1 in the output.

This module has no runtime dependencies beyond the Python standard library and
is safe to import from both the ``arena`` and ``web`` packages.

**Line-ending contract**: test case content is treated as UTF-8 text with Unix
line endings (LF only). ``normalize_testcase_bytes`` and
``normalize_testcase_text`` enforce this contract; both are applied inside
``parse_testcases_zip`` and must also be called at every other write boundary
(form submissions, file writes, DB inserts).
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field

_TC_DIR_RE = re.compile(r"^(in|out)/0*([1-9]\d{0,2})(\.in|\.out|\.sol)?$", re.IGNORECASE)


def normalize_testcase_bytes(data: bytes) -> bytes:
    """Normalize CRLF and lone CR to LF in test case content bytes.

    Args:
        data: Raw test case bytes to normalize.

    Returns:
        bytes: Content with all ``\\r\\n`` and lone ``\\r`` replaced by ``\\n``.
    """
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def normalize_testcase_text(text: str) -> str:
    """Normalize CRLF and lone CR to LF in test case content text.

    Args:
        text: Raw test case string to normalize.

    Returns:
        str: Content with all ``\\r\\n`` and lone ``\\r`` replaced by ``\\n``.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


_TC_FLAT_RE = re.compile(r"^0*([1-9]\d{0,2})\.(in|out|sol)$", re.IGNORECASE)
_TC_EXP_RE = re.compile(r"^explanation/0*([1-9]\d{0,2})\.txt$", re.IGNORECASE)

_MAX_TESTCASES = 1000

#: Maximum normalized (LF) UTF-8 byte length of a single test-case side (input
#: or output) that may be edited inline through a textarea. Larger cases must be
#: edited offline via the single-case ZIP download/replace round-trip. This is
#: the single source of truth for the inline-edit gate in both Arena and Web.
MAX_INLINE_TESTCASE_BYTES = 10 * 1024


@dataclass
class ParsedTestCases:
    """Result of parsing a test-case ZIP archive.

    Ordinals are remapped contiguously starting at 1; ``pairs`` and
    ``explanations`` share the same remapped ordinal space.

    Attributes:
        pairs: Mapping of 1-based ordinal to ``(input_bytes, output_bytes)``.
            The output is ``None`` for an interactive problem's cases, which
            carry input only.
        explanations: Mapping of 1-based ordinal to the decoded explanation
            string. Only present for ordinals that had an ``explanation/NNN.txt``
            entry; ordinals without one are simply absent.
    """

    pairs: dict[int, tuple[bytes, bytes | None]]
    explanations: dict[int, str] = field(default_factory=dict)


def parse_testcases_zip(zip_bytes: bytes, *, require_output: bool = True) -> ParsedTestCases:
    """Parse a ZIP archive into test cases and optional explanations.

    Ordinals in the result are remapped so they start at 1 and are contiguous,
    regardless of gaps in the source filenames. Explanations follow the same
    remapping; an explanation file for an ordinal without a matching input is
    ignored.

    Args:
        zip_bytes: Raw bytes of the ZIP file to parse.
        require_output: False for an interactive problem, whose cases carry input
            only. Output files in the archive are then ignored and every parsed
            case gets a ``None`` output.

    Returns:
        ParsedTestCases: The remapped cases and explanations.

    Raises:
        ValueError: If the bytes are not a valid ZIP file, if (when outputs are
            required) any input file has no matching output or vice-versa, if no
            valid test cases are found, if the archive contains more than 1000
            test cases, or if an explanation file is not valid UTF-8.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid ZIP file.") from exc

    inputs: dict[int, bytes] = {}
    outputs: dict[int, bytes] = {}
    explanation_bytes: dict[int, bytes] = {}

    for name in archive.namelist():
        match_dir = _TC_DIR_RE.match(name)
        match_flat = _TC_FLAT_RE.match(name)
        match_exp = _TC_EXP_RE.match(name)

        if match_dir:
            direction = match_dir.group(1).lower()
            ordinal = int(match_dir.group(2))
            data = archive.read(name)
            if direction == "in":
                inputs[ordinal] = data
            else:
                outputs[ordinal] = data
        elif match_flat:
            ordinal = int(match_flat.group(1))
            direction = match_flat.group(2).lower()
            data = archive.read(name)
            if direction == "in":
                inputs[ordinal] = data
            else:
                outputs[ordinal] = data
        elif match_exp:
            ordinal = int(match_exp.group(1))
            explanation_bytes[ordinal] = archive.read(name)

    if require_output:
        kept_ordinals = sorted(set(inputs) & set(outputs))
        unpaired_in = set(inputs) - set(outputs)
        unpaired_out = set(outputs) - set(inputs)
        if unpaired_in:
            raise ValueError(f"Input files without matching output: ordinals {sorted(unpaired_in)}")
        if unpaired_out:
            raise ValueError(f"Output files without matching input: ordinals {sorted(unpaired_out)}")
    else:
        kept_ordinals = sorted(inputs)

    if not kept_ordinals:
        raise ValueError("No valid test cases found in ZIP.")
    if len(kept_ordinals) > _MAX_TESTCASES:
        raise ValueError(f"Too many test cases: {len(kept_ordinals)} (max {_MAX_TESTCASES}).")

    # Decode/validate explanations only for kept ordinals; orphan explanations
    # (no matching input) are ignored without being decoded or validated.
    pairs: dict[int, tuple[bytes, bytes | None]] = {}
    remapped_explanations: dict[int, str] = {}
    for new_ordinal, old_ordinal in enumerate(kept_ordinals, start=1):
        expected_output = normalize_testcase_bytes(outputs[old_ordinal]) if require_output else None
        pairs[new_ordinal] = (normalize_testcase_bytes(inputs[old_ordinal]), expected_output)
        if old_ordinal in explanation_bytes:
            remapped_explanations[new_ordinal] = _decode_explanation(explanation_bytes[old_ordinal], old_ordinal)
    return ParsedTestCases(pairs=pairs, explanations=remapped_explanations)


def _decode_explanation(data: bytes, ordinal: int) -> str:
    """Decode and validate a single explanation file's bytes.

    Args:
        data: Raw bytes of the ``explanation/NNN.txt`` file.
        ordinal: Source ordinal, used only for error messages.

    Returns:
        str: The decoded explanation text.

    Raises:
        ValueError: If the bytes are not valid UTF-8.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Explanation for ordinal {ordinal} is not valid UTF-8.") from exc
    return text


@dataclass
class SingleTestCase:
    """A single test case parsed from (or destined for) a one-case ZIP.

    Attributes:
        input_bytes: Normalized (LF) UTF-8 input content.
        output_bytes: Normalized (LF) UTF-8 expected-output content, ``None`` for
            an interactive problem's case.
        explanation: Optional author note, ``None`` when absent.
    """

    input_bytes: bytes
    output_bytes: bytes | None
    explanation: str | None = None


_SINGLE_TC_NAMES = {"input.txt": "input", "output.txt": "output", "explanation.txt": "explanation"}


def parse_single_testcase_zip(zip_bytes: bytes, *, require_output: bool = True) -> SingleTestCase:
    """Parse a single-case ZIP with ``input.txt`` / ``output.txt`` entries.

    Entry names are matched case-insensitively. ``input.txt`` is always required
    and ``output.txt`` is required unless the problem is interactive;
    ``explanation.txt`` is optional. Content bytes are normalized to LF. There is
    no size cap — this is the supported offline path for large test cases.

    Args:
        zip_bytes: Raw bytes of the uploaded ZIP file.
        require_output: False for an interactive problem, whose case carries input
            only. ``output.txt`` is then ignored if present.

    Returns:
        SingleTestCase: The parsed and normalized case plus optional explanation.

    Raises:
        ValueError: If the bytes are not a valid ZIP, if a required entry is
            missing, if a side is not valid UTF-8, or if the explanation is not
            valid UTF-8 or exceeds the character limit.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid ZIP file.") from exc

    found: dict[str, bytes] = {}
    for name in archive.namelist():
        key = _SINGLE_TC_NAMES.get(name.rsplit("/", 1)[-1].lower())
        if key is not None and key not in found:
            found[key] = archive.read(name)

    if "input" not in found:
        raise ValueError("Single-case ZIP must contain input.txt.")
    if require_output and "output" not in found:
        raise ValueError("Single-case ZIP must contain output.txt.")

    sides = ("input", "output") if require_output else ("input",)
    for side in sides:
        try:
            found[side].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{side}.txt is not valid UTF-8 text.") from exc

    explanation: str | None = None
    if "explanation" in found:
        text = _decode_explanation(found["explanation"], 1)
        explanation = text.strip() or None

    return SingleTestCase(
        input_bytes=normalize_testcase_bytes(found["input"]),
        output_bytes=normalize_testcase_bytes(found["output"]) if require_output else None,
        explanation=explanation,
    )


def build_single_testcase_zip(input_bytes: bytes, output_bytes: bytes | None, explanation: str | None) -> bytes:
    """Build a single-case download ZIP with clear ``input.txt`` / ``output.txt``.

    Args:
        input_bytes: Input content (written as-is).
        output_bytes: Expected-output content (written as-is), or ``None`` for an
            interactive problem's case, whose ZIP carries no ``output.txt``.
        explanation: Optional author note; included as ``explanation.txt`` when
            non-empty.

    Returns:
        bytes: The in-memory ZIP archive contents.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("input.txt", input_bytes)
        if output_bytes is not None:
            archive.writestr("output.txt", output_bytes)
        if explanation:
            archive.writestr("explanation.txt", explanation.encode("utf-8"))
    return buffer.getvalue()
