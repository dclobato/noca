#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared classification and parsing for multi-case test-case archives."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from shared.services.problem_package.constants import (
    EXPLANATION_RE,
    MAX_TEST_CASE_ORDINAL,
    MAX_TEST_CASES,
    MIN_TEST_CASE_ORDINAL,
    TESTCASE_DIR_RE,
    TESTCASE_FLAT_RE,
)

TestCaseStream = Literal["input", "output", "explanation"]
TestCaseLayout = Literal["directory", "flat"]


@dataclass(frozen=True, slots=True)
class ClassifiedTestCaseMember:
    """One recognized multi-case archive member."""

    name: str
    stream: TestCaseStream
    ordinal: int
    layout: TestCaseLayout | None


@dataclass(frozen=True, slots=True)
class TestCaseMemberIndex:
    """Unique archive members indexed by logical stream and source ordinal."""

    inputs: dict[int, str]
    outputs: dict[int, str]
    explanations: dict[int, str]


@dataclass
class ParsedTestCases:
    """Remapped test-case byte pairs and their optional explanations."""

    pairs: dict[int, tuple[bytes, bytes | None]]
    explanations: dict[int, str] = field(default_factory=dict)


def classify_testcase_member(name: str) -> ClassifiedTestCaseMember | None:
    """Classify a test-case or explanation member and validate its ordinal."""
    match_dir = TESTCASE_DIR_RE.match(name)
    if match_dir:
        stream: TestCaseStream = "input" if match_dir.group(1).lower() == "in" else "output"
        return ClassifiedTestCaseMember(name, stream, _ordinal(match_dir.group(2), name), "directory")

    match_flat = TESTCASE_FLAT_RE.match(name)
    if match_flat:
        stream = "input" if match_flat.group(2).lower() == "in" else "output"
        return ClassifiedTestCaseMember(name, stream, _ordinal(match_flat.group(1), name), "flat")

    explanation = EXPLANATION_RE.match(name)
    if explanation:
        return ClassifiedTestCaseMember(name, "explanation", _ordinal(explanation.group(1), name), None)
    return None


def index_testcase_members(names: Iterable[str]) -> TestCaseMemberIndex:
    """Index members and reject mixed layouts or duplicate logical streams."""
    indexed: dict[TestCaseStream, dict[int, str]] = {
        "input": {},
        "output": {},
        "explanation": {},
    }
    layouts: set[TestCaseLayout] = set()
    for name in names:
        member = classify_testcase_member(name)
        if member is None:
            continue
        if member.layout is not None:
            layouts.add(member.layout)
        previous = indexed[member.stream].get(member.ordinal)
        if previous is not None:
            raise ValueError(
                f"Members {previous!r} and {name!r} both provide the {member.stream} "
                f"stream of test case {member.ordinal}."
            )
        indexed[member.stream][member.ordinal] = name

    if len(layouts) > 1:
        raise ValueError(
            "Archive mixes flat (001.in) and directory (in/001.in) test-case layouts; use one layout only."
        )
    return TestCaseMemberIndex(
        inputs=indexed["input"],
        outputs=indexed["output"],
        explanations=indexed["explanation"],
    )


def paired_testcase_ordinals(index: TestCaseMemberIndex, *, require_output: bool) -> list[int]:
    """Validate pairing and return the source ordinals that form test cases."""
    if require_output:
        unpaired_in = sorted(set(index.inputs) - set(index.outputs))
        unpaired_out = sorted(set(index.outputs) - set(index.inputs))
        if unpaired_in:
            raise ValueError(f"Input files without matching output: ordinals {unpaired_in}")
        if unpaired_out:
            raise ValueError(f"Output files without matching input: ordinals {unpaired_out}")
        kept = sorted(set(index.inputs) & set(index.outputs))
    else:
        kept = sorted(index.inputs)

    if not kept:
        raise ValueError("No valid test cases found in ZIP.")
    if len(kept) > MAX_TEST_CASES:
        raise ValueError(f"Too many test cases: {len(kept)} (max {MAX_TEST_CASES}).")
    return kept


def parse_testcases_zip(zip_bytes: bytes, *, require_output: bool = True) -> ParsedTestCases:
    """Parse a bare multi-case ZIP through the shared member contract."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid ZIP file.") from exc

    with archive:
        index = index_testcase_members(archive.namelist())
        kept = paired_testcase_ordinals(index, require_output=require_output)
        pairs: dict[int, tuple[bytes, bytes | None]] = {}
        explanations: dict[int, str] = {}
        for new_ordinal, source_ordinal in enumerate(kept, start=1):
            input_bytes = normalize_testcase_bytes(archive.read(index.inputs[source_ordinal]))
            output_bytes = (
                normalize_testcase_bytes(archive.read(index.outputs[source_ordinal])) if require_output else None
            )
            pairs[new_ordinal] = (input_bytes, output_bytes)
            explanation_name = index.explanations.get(source_ordinal)
            if explanation_name is not None:
                explanations[new_ordinal] = decode_testcase_explanation(archive.read(explanation_name), source_ordinal)
    return ParsedTestCases(pairs=pairs, explanations=explanations)


def normalize_testcase_bytes(data: bytes) -> bytes:
    """Normalize CRLF and lone CR to LF in test-case bytes."""
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def normalize_testcase_text(text: str) -> str:
    """Normalize CRLF and lone CR to LF in test-case text."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _ordinal(raw: str, name: str) -> int:
    """Return a supported source ordinal."""
    value = int(raw)
    if not MIN_TEST_CASE_ORDINAL <= value <= MAX_TEST_CASE_ORDINAL:
        raise ValueError(
            f"Archive member {name!r} uses ordinal {value}, outside the supported range "
            f"{MIN_TEST_CASE_ORDINAL}..{MAX_TEST_CASE_ORDINAL}."
        )
    return value


def decode_testcase_explanation(data: bytes, ordinal: int) -> str:
    """Decode one retained explanation."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Explanation for ordinal {ordinal} is not valid UTF-8.") from exc
