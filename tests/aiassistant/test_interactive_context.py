#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the interactive-review prompt-section builder."""

from __future__ import annotations

from aiassistant.db.interactive_queries import InteractiveContext
from aiassistant.interactive_context import _cap_lines, build_interactive_note


def _ctx(**overrides: object) -> InteractiveContext:
    base: dict[str, object] = {
        "verdict": "TLE",
        "failing_case_ordinal": 1,
        "crash_reason": None,
        "stderr_excerpt": None,
        "transcript_text": "> 10\n< 5\n> <\n< !4",
        "transcript_truncated": False,
        "sample_transcripts": [],
    }
    base.update(overrides)
    return InteractiveContext(**base)  # type: ignore[arg-type]


def test_cap_lines_keeps_head_and_tail() -> None:
    text = "\n".join(str(i) for i in range(10))
    capped, was_capped = _cap_lines(text, 2, 2)
    assert was_capped is True
    assert capped.splitlines() == ["0", "1", "... [6 lines omitted] ...", "8", "9"]


def test_cap_lines_noop_when_short() -> None:
    capped, was_capped = _cap_lines("a\nb\nc", 5, 5)
    assert was_capped is False
    assert capped == "a\nb\nc"


def test_note_includes_verdict_case_and_wrapped_transcript() -> None:
    note = build_interactive_note(_ctx())
    assert note.startswith("<interactive_context>")
    assert note.endswith("</interactive_context>")
    assert "Verdict for this submission: TLE on secret test case #1." in note
    assert "<untrusted_recorded_interaction>" in note
    assert "> 10" in note and "< !4" in note


def test_note_marks_truncated_and_includes_crash_and_stderr() -> None:
    note = build_interactive_note(
        _ctx(transcript_truncated=True, crash_reason="validator_killed_contestant", stderr_excerpt="boom")
    )
    assert "partial excerpt" in note
    assert "Judge crash reason: validator_killed_contestant." in note
    assert "[program stderr excerpt]\nboom" in note


def test_note_includes_capped_samples() -> None:
    note = build_interactive_note(
        _ctx(
            sample_transcripts=[
                ("> 1\n< !1", "spot on"),
                ("> 2\n< !2", None),
                ("> 3\n< !3", None),
                ("> 4\n< !4", None),
            ]
        )
    )
    assert "<untrusted_sample_interactions>" in note
    assert "--- Example 1 ---" in note
    assert "(explanation: spot on)" in note
    # Capped at three samples.
    assert "--- Example 3 ---" in note
    assert "--- Example 4 ---" not in note


def test_note_empty_when_no_transcript_and_no_samples() -> None:
    note = build_interactive_note(_ctx(transcript_text=None, sample_transcripts=[]))
    assert note == ""


def test_note_without_transcript_still_shows_samples() -> None:
    note = build_interactive_note(_ctx(transcript_text=None, sample_transcripts=[("> 1\n< !1", None)]))
    assert "<untrusted_sample_interactions>" in note
    assert "<untrusted_recorded_interaction>" not in note
