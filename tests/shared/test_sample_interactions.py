#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for the domain-neutral sample-interaction service."""

from __future__ import annotations

import json

import pytest

from shared.services.sample_interactions import (
    MAX_SAMPLE_INTERACTIONS,
    InteractionParseError,
    build_interaction_files,
    interactive_testcase_violation,
    parse_interaction_text,
    parse_packaged_interactions,
    transcript_line_count,
    transcript_preview,
    transcript_to_text,
    validate_transcript_json,
)

# ── parse_interaction_text ────────────────────────────────────────────────────


def test_parse_maps_the_two_prefixes_to_the_transcript_directions() -> None:
    """'>' is the validator speaking; '<' is the contestant's program."""
    transcript = parse_interaction_text("> 3\n< 5\n> !8")

    assert transcript == {
        "lines": [
            {"dir": "validator", "line": "3"},
            {"dir": "user", "line": "5"},
            {"dir": "validator", "line": "!8"},
        ],
        "truncated": False,
    }


def test_parse_preserves_everything_after_the_two_character_prefix() -> None:
    """Only the prefix is consumed: inner spacing and markers survive verbatim."""
    transcript = parse_interaction_text(">   3   spaces\n< > not a prefix")
    lines = transcript["lines"]
    assert isinstance(lines, list)

    assert lines[0] == {"dir": "validator", "line": "  3   spaces"}
    assert lines[1] == {"dir": "user", "line": "> not a prefix"}


def test_parse_accepts_an_empty_protocol_line_written_as_a_bare_prefix() -> None:
    """A line that is exactly '> ' means the validator sent an empty line."""
    transcript = parse_interaction_text("> \n< x")
    lines = transcript["lines"]
    assert isinstance(lines, list)

    assert lines[0] == {"dir": "validator", "line": ""}


def test_parse_normalizes_crlf_and_cr_line_endings() -> None:
    """A transcript pasted from Windows parses the same as a Unix one."""
    assert parse_interaction_text("> 3\r\n< 5") == parse_interaction_text("> 3\n< 5")
    assert parse_interaction_text("> 3\r< 5") == parse_interaction_text("> 3\n< 5")


def test_parse_tolerates_exactly_one_trailing_newline() -> None:
    """A single trailing newline is normal textarea output, not a malformed line."""
    assert parse_interaction_text("> 3\n< 5\n") == parse_interaction_text("> 3\n< 5")


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("> 3\n< 5\n\n", id="one blank line past the final newline"),
        pytest.param("> 3\n< 5\n\n\n", id="several trailing blank lines"),
    ],
)
def test_parse_rejects_blank_lines_past_the_final_newline(text: str) -> None:
    """Only the textarea's own newline is forgiven; a typed blank line is malformed."""
    with pytest.raises(InteractionParseError):
        parse_interaction_text(text)


def test_parse_keeps_trailing_spaces_on_a_protocol_line() -> None:
    """Trailing spaces are part of what the program wrote, so they survive verbatim."""
    transcript = parse_interaction_text("> 3  \n< 5\t")
    lines = transcript["lines"]
    assert isinstance(lines, list)

    assert lines[0]["line"] == "3  "
    assert lines[1]["line"] == "5\t"


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(">3", id="no space after validator marker"),
        pytest.param("<5", id="no space after user marker"),
        pytest.param(">", id="bare validator marker"),
        pytest.param("<", id="bare user marker"),
        pytest.param(" > 3", id="leading whitespace before marker"),
        pytest.param("\t> 3", id="leading tab before marker"),
        pytest.param("> 3\n\n< 5", id="blank line inside the conversation"),
        pytest.param("> 3\n5", id="line with no marker at all"),
        pytest.param("3", id="plain text"),
    ],
)
def test_parse_rejects_every_line_that_is_not_an_exact_prefix(text: str) -> None:
    """The prefix contract is strict: no bare markers, no blanks, no indentation."""
    with pytest.raises(InteractionParseError):
        parse_interaction_text(text)


def test_parse_error_names_the_offending_line_number() -> None:
    """The author is told which line to fix, counting from 1."""
    with pytest.raises(InteractionParseError, match="Line 3"):
        parse_interaction_text("> 3\n< 5\nbroken")


@pytest.mark.parametrize("text", ["", "   ", "\n\n"])
def test_parse_rejects_an_empty_transcript(text: str) -> None:
    """An interaction with no conversation in it is not an interaction."""
    with pytest.raises(InteractionParseError):
        parse_interaction_text(text)


# ── transcript_to_text and previews ───────────────────────────────────────────


def test_transcript_to_text_round_trips_through_the_parser() -> None:
    """What the edit form shows is exactly what the author would have typed."""
    original = "> 3\n<   5  \n> !8\n> "

    assert transcript_to_text(parse_interaction_text(original)) == original.rstrip("\n")


def test_transcript_preview_shows_the_opening_lines_with_their_markers() -> None:
    """The admin list previews the opening moves of the conversation."""
    transcript = parse_interaction_text("> 3\n< 5")

    assert transcript_preview(transcript) == "> 3\n< 5"
    assert transcript_line_count(transcript) == 2


def test_transcript_preview_stops_at_the_line_budget() -> None:
    """A long conversation is previewed up to the budget, then marked as cut."""
    transcript = parse_interaction_text("\n".join(f"> {n}" for n in range(12)))

    preview = transcript_preview(transcript, max_lines=10)

    assert preview.splitlines() == [f"> {n}" for n in range(10)] + ["…"]


def test_transcript_preview_keeps_a_short_conversation_unmarked() -> None:
    """Exactly the budget of lines is the whole conversation, so nothing is cut."""
    transcript = parse_interaction_text("\n".join(f"> {n}" for n in range(10)))

    assert transcript_preview(transcript, max_lines=10).splitlines() == [f"> {n}" for n in range(10)]


def test_transcript_preview_truncates_a_long_line() -> None:
    """A long line cannot blow out the admin list column."""
    preview = transcript_preview(parse_interaction_text(f"> {'x' * 200}"), limit=20)

    assert len(preview) == 20
    assert preview.endswith("…")


# ── validate_transcript_json ──────────────────────────────────────────────────


def test_validate_transcript_json_accepts_a_well_formed_transcript() -> None:
    """A transcript exported by NOCA re-imports unchanged."""
    original = parse_interaction_text("> 3\n< 5")

    assert validate_transcript_json(json.loads(json.dumps(original))) == original


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param([], id="not an object"),
        pytest.param({}, id="no lines key"),
        pytest.param({"lines": []}, id="empty lines"),
        pytest.param({"lines": ["> 3"]}, id="line is not an object"),
        pytest.param({"lines": [{"dir": "judge", "line": "3"}]}, id="unknown direction"),
        pytest.param({"lines": [{"dir": "user"}]}, id="missing line"),
        pytest.param({"lines": [{"dir": "user", "line": 3}]}, id="line is not a string"),
        pytest.param({"lines": [{"dir": "user", "line": "3"}], "truncated": "no"}, id="truncated not a bool"),
    ],
)
def test_validate_transcript_json_rejects_a_malformed_transcript(payload: object) -> None:
    """A hand-edited package cannot smuggle a broken transcript into the database."""
    with pytest.raises(InteractionParseError):
        validate_transcript_json(payload)


# ── package round-trip ────────────────────────────────────────────────────────


def _package(members: dict[str, bytes]) -> list[object]:
    """Parse an in-memory set of archive members."""
    return list(parse_packaged_interactions(archive_names=set(members), read_file=members.__getitem__))


def test_parse_packaged_interactions_reads_transcripts_and_explanations() -> None:
    """A package's interaction/ members become ordered interactions."""
    members = {
        "interaction/001.interaction": json.dumps(parse_interaction_text("> 3\n< 5")).encode(),
        "interaction/001.explain": b"  The validator opens.  ",
        "interaction/002.interaction": json.dumps(parse_interaction_text("> 9")).encode(),
    }

    parsed = _package(members)

    assert len(parsed) == 2
    assert parsed[0].explanation == "The validator opens."  # type: ignore[attr-defined]
    assert parsed[1].explanation is None  # type: ignore[attr-defined]
    assert transcript_to_text(parsed[1].transcript) == "> 9"  # type: ignore[attr-defined]


def test_parse_packaged_interactions_remaps_gapped_ordinals_contiguously() -> None:
    """Gaps in a hand-built package close, matching how test cases are remapped."""
    members = {
        "interaction/007.interaction": json.dumps(parse_interaction_text("> first")).encode(),
        "interaction/042.interaction": json.dumps(parse_interaction_text("> second")).encode(),
    }

    parsed = _package(members)

    assert [transcript_to_text(item.transcript) for item in parsed] == ["> first", "> second"]  # type: ignore[attr-defined]


def test_parse_packaged_interactions_ignores_an_orphan_explanation() -> None:
    """An .explain with no .interaction beside it is dropped, not an error."""
    members = {
        "interaction/001.interaction": json.dumps(parse_interaction_text("> 3")).encode(),
        "interaction/009.explain": b"nothing to explain",
    }

    assert len(_package(members)) == 1


def test_parse_packaged_interactions_rejects_more_than_the_cap() -> None:
    """A package cannot exceed the per-problem interaction limit."""
    members = {
        f"interaction/{ordinal:03d}.interaction": json.dumps(parse_interaction_text("> x")).encode()
        for ordinal in range(1, MAX_SAMPLE_INTERACTIONS + 2)
    }

    with pytest.raises(InteractionParseError, match="at most"):
        _package(members)


@pytest.mark.parametrize("suffix", ["interaction", "explain"])
def test_parse_packaged_interactions_rejects_ordinal_aliases(suffix: str) -> None:
    content = json.dumps(parse_interaction_text("> x")).encode() if suffix == "interaction" else b"why"
    members = {
        f"interaction/1.{suffix}": content,
        f"interaction/001.{suffix}": content,
    }

    with pytest.raises(InteractionParseError, match="both provide"):
        _package(members)


def test_parse_packaged_interactions_rejects_an_ordinal_above_the_shared_range() -> None:
    members = {"interaction/5000.interaction": json.dumps(parse_interaction_text("> x")).encode()}

    with pytest.raises(InteractionParseError, match="outside the supported range"):
        _package(members)


def test_parse_packaged_interactions_rejects_invalid_json() -> None:
    """A corrupt .interaction file fails the import rather than importing garbage."""
    members = {"interaction/001.interaction": b"{not json"}

    with pytest.raises(InteractionParseError, match="valid JSON"):
        _package(members)


def test_build_interaction_files_numbers_sequentially_and_skips_empty_explanations() -> None:
    """Export numbering reflects display order, and writes no empty .explain files."""
    members = build_interaction_files(
        [
            (parse_interaction_text("> 3"), "explained"),
            (parse_interaction_text("< 5"), None),
        ]
    )

    assert [name for name, _ in members] == [
        "interaction/001.interaction",
        "interaction/001.explain",
        "interaction/002.interaction",
    ]


def test_export_import_round_trips_a_transcript() -> None:
    """What build_interaction_files writes, parse_packaged_interactions reads back."""
    original = parse_interaction_text("> 3\n< 5\n> !8")
    members = dict(build_interaction_files([(original, "why")]))

    parsed = _package(members)

    assert parsed[0].transcript == original  # type: ignore[attr-defined]
    assert parsed[0].explanation == "why"  # type: ignore[attr-defined]


# ── interactive test-case invariant ───────────────────────────────────────────


def test_invariant_rejects_a_public_case_on_an_interactive_problem() -> None:
    """An interactive problem shows sample interactions, never sample test cases."""
    assert interactive_testcase_violation(total_cases=3, sample_cases=1) is not None


def test_invariant_rejects_an_interactive_problem_with_no_cases() -> None:
    """The validator still needs at least one secret case to be replayed against."""
    assert interactive_testcase_violation(total_cases=0, sample_cases=0) is not None


def test_invariant_accepts_secret_only_cases() -> None:
    """Secret cases and no public ones is exactly what an interactive problem wants."""
    assert interactive_testcase_violation(total_cases=1, sample_cases=0) is None
