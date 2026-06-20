from datetime import UTC, datetime, timedelta

from web.services.assorted_utils import minutes_from_contest_start, render_prettytable, slugfy


def test_minutes_from_contest_start_exact_boundary() -> None:
    contest_start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    requested_at = contest_start + timedelta(minutes=7)

    assert minutes_from_contest_start(contest_start, requested_at) == 7


def test_minutes_from_contest_start_truncates_subminute_seconds() -> None:
    contest_start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    requested_at = contest_start + timedelta(minutes=7, seconds=59)

    assert minutes_from_contest_start(contest_start, requested_at) == 7


def test_minutes_from_contest_start_rejects_negative_elapsed_time() -> None:
    contest_start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    requested_at = contest_start - timedelta(seconds=1)

    assert minutes_from_contest_start(contest_start, requested_at) == -1


def test_slugfy_replaces_invalid_runs_with_underscores() -> None:
    assert slugfy(" user / export?.json ") == "user_export_.json"


def test_slugfy_uses_fallback_when_value_is_blank() -> None:
    assert slugfy("   ", fallback="contest") == "contest"


def test_render_prettytable_supports_column_specific_alignment_and_wrapping() -> None:
    rendered = render_prettytable(
        ["Time", "Description"],
        [["5", "A very long description that should wrap across multiple lines."]],
        header_alignments={"Time": "r"},
        max_widths={"Description": 20},
    )

    assert "|   Time | Description        |" in rendered
    assert "|      5 | A very long" in rendered
    assert "description that" in rendered
