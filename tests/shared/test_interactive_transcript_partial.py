#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Rendering of the shared interactive-attempt diagnostic partial.

Both the Contest/Arena submission review pages and the solution-test detail
page feed this partial one attempt row; these tests render it directly rather
than through either caller's full page.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jinja2 import ChainableUndefined, Environment, FileSystemLoader

_ROOT = Path(__file__).resolve().parents[2]


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader([str(_ROOT / "shared" / "template")]),
        undefined=ChainableUndefined,
        autoescape=True,
    )


def _attempt(**overrides: Any) -> SimpleNamespace:
    base = dict(
        test_case_ordinal=1,
        attempt_number=1,
        contestant_exit_code=1,
        contestant_signal=None,
        validator_exit_code=0,
        validator_signal=None,
        crash_reason=None,
        validator_verdict=SimpleNamespace(value="AC"),
        limit_outcome=None,
        transcript=None,
        contestant_stderr_excerpt=None,
        validator_stderr_excerpt=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _render(attempt: SimpleNamespace) -> str:
    return _env().get_template("_partials/interactive_transcript.html").render(attempt=attempt)


def test_clean_validator_verdict_renders_when_no_limit_was_enforced() -> None:
    """A validator's own AC/WA/... reading is meaningful on an ordinary case."""
    markup = _render(_attempt())
    assert "Crash reason / clean verdict" in markup
    assert "AC" in markup


def test_clean_verdict_is_suppressed_once_a_limit_decided_the_case() -> None:
    """An MLE/OLE/contestant-TLE case can leave the validator reporting a stale AC.

    A validator that never saw the limit hit can still report AC from its own
    process's exit code; showing that alongside the enforced limit would read
    as if the case passed.
    """
    markup = _render(_attempt(limit_outcome="MLE"))
    assert "AC" not in markup
    assert "Limit outcome" in markup
    assert "MLE" in markup


def test_crash_reason_always_wins_over_a_clean_verdict() -> None:
    """The two outcomes are mutually exclusive by constraint; crash always shows."""
    markup = _render(_attempt(crash_reason=SimpleNamespace(value="SIGNAL"), validator_verdict=None))
    assert "SIGNAL" in markup
