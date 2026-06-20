#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the shared test-case ZIP parser, including explanations."""

from __future__ import annotations

import io
import zipfile

import pytest

from shared.tc_zip import (
    MAX_INLINE_TESTCASE_BYTES,
    ParsedTestCases,
    build_single_testcase_zip,
    normalize_testcase_bytes,
    normalize_testcase_text,
    parse_single_testcase_zip,
    parse_testcases_zip,
)


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_parse_directory_layout_sol_extension_accepted_as_output() -> None:
    parsed = parse_testcases_zip(_zip({"in/001.in": b"1\n", "out/001.sol": b"2\n"}))

    assert parsed.pairs == {1: (b"1\n", b"2\n")}


def test_parse_flat_layout_sol_extension_accepted_as_output() -> None:
    parsed = parse_testcases_zip(_zip({"001.in": b"x\n", "001.sol": b"y\n"}))

    assert parsed.pairs == {1: (b"x\n", b"y\n")}


def test_parse_mixed_out_and_sol_extensions_across_pairs() -> None:
    parsed = parse_testcases_zip(
        _zip(
            {
                "001.in": b"a\n",
                "001.out": b"A\n",
                "002.in": b"b\n",
                "002.sol": b"B\n",
            }
        )
    )

    assert parsed.pairs == {1: (b"a\n", b"A\n"), 2: (b"b\n", b"B\n")}


def test_parse_without_explanations_yields_empty_explanations() -> None:
    parsed = parse_testcases_zip(_zip({"in/001.in": b"1\n", "out/001.out": b"2\n"}))

    assert isinstance(parsed, ParsedTestCases)
    assert parsed.pairs == {1: (b"1\n", b"2\n")}
    assert parsed.explanations == {}


def test_explanations_are_collected_and_remapped_with_pairs() -> None:
    # Source ordinals 5 and 9 are remapped to contiguous 1 and 2.
    parsed = parse_testcases_zip(
        _zip(
            {
                "in/005.in": b"a\n",
                "out/005.out": b"A\n",
                "explanation/005.txt": b"why A",
                "in/009.in": b"b\n",
                "out/009.out": b"B\n",
                "explanation/009.txt": b"why B",
            }
        )
    )

    assert parsed.pairs == {1: (b"a\n", b"A\n"), 2: (b"b\n", b"B\n")}
    assert parsed.explanations == {1: "why A", 2: "why B"}


def test_explanation_for_unpaired_ordinal_is_ignored() -> None:
    parsed = parse_testcases_zip(
        _zip(
            {
                "in/001.in": b"x\n",
                "out/001.out": b"y\n",
                "explanation/002.txt": b"orphan",
            }
        )
    )

    assert parsed.pairs == {1: (b"x\n", b"y\n")}
    assert parsed.explanations == {}


def test_invalid_orphan_explanation_does_not_reject_valid_pairs() -> None:
    # An orphan explanation that is invalid UTF-8 / overlength must not be
    # decoded or validated, so the otherwise-valid ZIP still imports.
    parsed = parse_testcases_zip(
        _zip(
            {
                "in/001.in": b"x\n",
                "out/001.out": b"y\n",
                "explanation/002.txt": b"\xff\xfe" + b"z" * 2000,
            }
        )
    )

    assert parsed.pairs == {1: (b"x\n", b"y\n")}
    assert parsed.explanations == {}


def test_long_explanation_is_accepted() -> None:
    # The former 1024-character cap was removed; explanations are now unbounded.
    long_text = ("x" * 5000).encode("utf-8")
    parsed = parse_testcases_zip(_zip({"in/001.in": b"1\n", "out/001.out": b"2\n", "explanation/001.txt": long_text}))

    assert parsed.explanations == {1: "x" * 5000}


def test_invalid_utf8_explanation_is_rejected() -> None:
    with pytest.raises(ValueError, match="not valid UTF-8"):
        parse_testcases_zip(_zip({"in/001.in": b"1\n", "out/001.out": b"2\n", "explanation/001.txt": b"\xff\xfe"}))


# ── normalize_testcase_bytes ──────────────────────────────────────────────────


def test_normalize_testcase_bytes_crlf() -> None:
    assert normalize_testcase_bytes(b"P\r\n") == b"P\n"


def test_normalize_testcase_bytes_lone_cr() -> None:
    assert normalize_testcase_bytes(b"P\rQ\r") == b"P\nQ\n"


def test_normalize_testcase_bytes_empty_line_preserved() -> None:
    assert normalize_testcase_bytes(b"a\r\n\r\nb\r\n") == b"a\n\nb\n"


def test_normalize_testcase_bytes_already_lf_unchanged() -> None:
    data = b"line1\nline2\n"
    assert normalize_testcase_bytes(data) == data


def test_normalize_testcase_bytes_empty_string_preserved() -> None:
    assert normalize_testcase_bytes(b"") == b""


# ── normalize_testcase_text ───────────────────────────────────────────────────


def test_normalize_testcase_text_crlf() -> None:
    assert normalize_testcase_text("P\r\n") == "P\n"


def test_normalize_testcase_text_lone_cr() -> None:
    assert normalize_testcase_text("P\rQ\r") == "P\nQ\n"


def test_normalize_testcase_text_empty_line_preserved() -> None:
    assert normalize_testcase_text("a\r\n\r\nb\r\n") == "a\n\nb\n"


def test_normalize_testcase_text_already_lf_unchanged() -> None:
    text = "line1\nline2\n"
    assert normalize_testcase_text(text) == text


def test_normalize_testcase_text_empty_string_preserved() -> None:
    assert normalize_testcase_text("") == ""


# ── parse_testcases_zip CRLF normalization ────────────────────────────────────


def test_parse_normalizes_crlf_in_input_and_output() -> None:
    parsed = parse_testcases_zip(_zip({"in/001.in": b"P\r\n", "out/001.out": b"YES\r\n"}))

    assert parsed.pairs == {1: (b"P\n", b"YES\n")}


def test_parse_normalizes_lone_cr_in_input_and_output() -> None:
    parsed = parse_testcases_zip(_zip({"in/001.in": b"A\rB\r", "out/001.out": b"C\r"}))

    assert parsed.pairs == {1: (b"A\nB\n", b"C\n")}


def test_parse_normalizes_crlf_preserves_empty_lines() -> None:
    parsed = parse_testcases_zip(_zip({"in/001.in": b"a\r\n\r\nb\r\n", "out/001.out": b"1\r\n"}))

    assert parsed.pairs[1][0] == b"a\n\nb\n"


# ── single-case ZIP helpers ───────────────────────────────────────────────────


def test_parse_single_testcase_zip_valid() -> None:
    single = parse_single_testcase_zip(_zip({"input.txt": b"1 2\n", "output.txt": b"3\n", "explanation.txt": b"sum"}))

    assert single.input_bytes == b"1 2\n"
    assert single.output_bytes == b"3\n"
    assert single.explanation == "sum"


def test_parse_single_testcase_zip_case_insensitive_and_normalizes() -> None:
    single = parse_single_testcase_zip(_zip({"INPUT.TXT": b"a\r\nb\r\n", "Output.txt": b"ok\r\n"}))

    assert single.input_bytes == b"a\nb\n"
    assert single.output_bytes == b"ok\n"
    assert single.explanation is None


def test_parse_single_testcase_zip_missing_output_raises() -> None:
    with pytest.raises(ValueError, match="output.txt"):
        parse_single_testcase_zip(_zip({"input.txt": b"1\n"}))


def test_parse_single_testcase_zip_oversize_allowed() -> None:
    big = b"x" * (MAX_INLINE_TESTCASE_BYTES + 50)
    single = parse_single_testcase_zip(_zip({"input.txt": big, "output.txt": b"1\n"}))

    assert len(single.input_bytes) == len(big)


def test_parse_single_testcase_zip_bad_utf8_raises() -> None:
    with pytest.raises(ValueError, match="not valid UTF-8"):
        parse_single_testcase_zip(_zip({"input.txt": b"\xff\xfe", "output.txt": b"1\n"}))


def test_build_single_testcase_zip_round_trip() -> None:
    zip_bytes = build_single_testcase_zip(b"in\n", b"out\n", "why")
    single = parse_single_testcase_zip(zip_bytes)

    assert single.input_bytes == b"in\n"
    assert single.output_bytes == b"out\n"
    assert single.explanation == "why"


def test_build_single_testcase_zip_omits_blank_explanation() -> None:
    zip_bytes = build_single_testcase_zip(b"in\n", b"out\n", None)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        assert "explanation.txt" not in archive.namelist()
