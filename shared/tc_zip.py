#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared utility for parsing **bulk test-case** ZIP archives.

Problem packages and bare bulk test-case uploads use the same classifier,
pairing rules, ordinal checks, and normalization implementation in
``shared.services.problem_package.testcase_archive``. This module preserves the
historical public imports and owns only the separate single-case ZIP helpers.

Supports two ZIP layouts:

* **Directory layout** — ``in/001.in`` / ``out/001.out`` (or ``out/001.sol``)
* **Flat layout** — ``001.in`` / ``001.out`` (or ``001.sol``)

Leading zeros in filenames are stripped so ``001.in`` and ``1.in`` refer to the
same ordinal.  Ordinals are remapped contiguously starting at 1 in the output.

**Line-ending contract**: test case content is treated as UTF-8 text with Unix
line endings (LF only). ``normalize_testcase_bytes`` and
``normalize_testcase_text`` enforce this contract; both are applied inside
``parse_testcases_zip`` and must also be called at every other write boundary
(form submissions, file writes, DB inserts).
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

from shared.services.problem_package.testcase_archive import (
    ParsedTestCases,
    decode_testcase_explanation,
    normalize_testcase_bytes,
    normalize_testcase_text,
    parse_testcases_zip,
)

__all__ = [
    "MAX_INLINE_TESTCASE_BYTES",
    "ParsedTestCases",
    "SingleTestCase",
    "build_single_testcase_zip",
    "normalize_testcase_bytes",
    "normalize_testcase_text",
    "parse_single_testcase_zip",
    "parse_testcases_zip",
]

#: Maximum normalized (LF) UTF-8 byte length of a single test-case side (input
#: or output) that may be edited inline through a textarea. Larger cases must be
#: edited offline via the single-case ZIP download/replace round-trip. This is
#: the single source of truth for the inline-edit gate in both Arena and Web.
MAX_INLINE_TESTCASE_BYTES = 10 * 1024


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
        text = decode_testcase_explanation(found["explanation"], 1)
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
