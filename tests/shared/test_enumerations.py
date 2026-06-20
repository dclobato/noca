#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for shared enumerations."""

from shared.enumerations import (
    ARENA_AI_BATCH_JOB_TERMINAL_STATUSES,
    VERDICT_BADGE_CLASSES,
    VERDICT_LABELS,
    ArenaAIBatchJobStatus,
    Verdict,
)


def test_verdict_labels_cover_all_verdicts() -> None:
    """Every verdict code should have a display label."""
    assert set(VERDICT_LABELS) == {verdict.value for verdict in Verdict}


def test_verdict_labels_keep_existing_display_text() -> None:
    """Verdict display labels should stay stable for templates."""
    assert {
        Verdict.AC.value: "Accepted",
        Verdict.PE.value: "Presentation Error",
        Verdict.WA.value: "Wrong Answer",
        Verdict.TLE.value: "Time Limit Exceeded",
        Verdict.MLE.value: "Memory Limit Exceeded",
        Verdict.OLE.value: "Output Limit Exceeded",
        Verdict.RE.value: "Runtime Error",
        Verdict.CE.value: "Compilation Error",
    } == VERDICT_LABELS


def test_verdict_badge_classes_cover_all_verdicts() -> None:
    """Every verdict code should have a default badge class."""
    assert set(VERDICT_BADGE_CLASSES) == {verdict.value for verdict in Verdict}


def test_verdict_badge_classes_keep_existing_defaults() -> None:
    """Verdict badge classes should keep their canonical defaults."""
    assert {
        Verdict.AC.value: "bg-success",
        Verdict.PE.value: "bg-danger",
        Verdict.WA.value: "bg-danger",
        Verdict.TLE.value: "bg-warning text-dark",
        Verdict.MLE.value: "bg-warning text-dark",
        Verdict.OLE.value: "bg-warning text-dark",
        Verdict.RE.value: "bg-warning text-dark",
        Verdict.CE.value: "bg-secondary",
    } == VERDICT_BADGE_CLASSES


def test_arena_ai_batch_job_status_values_cover_local_state_machine() -> None:
    """AI batch job statuses should match the durable local state machine."""
    assert {status.value for status in ArenaAIBatchJobStatus} == {
        "staged",
        "preparing",
        "submitted",
        "polling",
        "expiring",
        "completed",
        "failed",
        "expired",
        "cancelled",
    }


def test_arena_ai_batch_terminal_statuses_exclude_active_states() -> None:
    """Only terminal AI batch statuses should be included in the terminal set."""
    assert {
        "completed",
        "failed",
        "expired",
        "cancelled",
    } == ARENA_AI_BATCH_JOB_TERMINAL_STATUSES
