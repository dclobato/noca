#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""An interactive problem's test-case table shows input only.

Its cases carry no expected output -- the input parametrizes the validator, which
decides the verdict -- and every case is secret, because a bare input would reveal
a secret without showing what to do with it. Contestants see sample interactions
instead, so the table drops the Output column, its sizes, and the sample toggle.

The flag follows the *stored strategy*, never validator-source presence: a problem
whose validator was removed is still interactive.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, select_autoescape

# Aliased: pytest would otherwise try to collect the imported `TestCaseRowView`
# as a test class and warn about its constructor.
from shared.services.testcase_view import TestCaseRowView as RowView

_ROOT = Path(__file__).resolve().parents[2]


def _render(*, interactive: bool) -> str:
    """Render the shared test-case table for one strategy."""
    env = Environment(
        loader=ChoiceLoader(
            [
                FileSystemLoader(_ROOT / "arena" / "template"),
                FileSystemLoader(_ROOT / "shared" / "template"),
            ]
        ),
        autoescape=select_autoescape(["html"]),
    )
    row = RowView(
        id="tc-1",
        ordinal=1,
        is_sample=True,
        has_explanation=False,
        input_preview="3 4",
        output_preview="7",
        input_size_bytes=3,
        output_size_bytes=1,
        is_large=False,
        edit_url="/edit",
        download_url="/download",
        replace_url="/replace",
        move_url="/move",
        toggle_sample_url="/toggle",
        delete_url="/delete",
    )
    return env.get_template("_partials/testcase_list_table.html").render(
        rows=[row],
        is_edit_allowed=True,
        interactive=interactive,
    )


def test_a_standard_table_keeps_its_output_column() -> None:
    """The baseline: a standard problem compares against a fixed expected output."""
    markup = _render(interactive=False)

    assert "Output (preview)" in markup
    assert ">sample<" in markup
    assert "/toggle" in markup


@pytest.mark.parametrize("fragment", ["Output (preview)", "/toggle", "swap_horiz"])
def test_an_interactive_table_drops_output_and_the_sample_toggle(fragment: str) -> None:
    """No expected output exists to show, and no case may be made public."""
    markup = _render(interactive=True)

    assert fragment not in markup


def test_every_interactive_case_reads_as_secret() -> None:
    """A stored sample flag must not surface: interactive cases are all secret."""
    markup = _render(interactive=True)

    assert ">secret<" in markup
    assert ">sample<" not in markup


def test_the_interactive_table_still_shows_the_input_and_its_size() -> None:
    """Dropping the output column must not take the input with it."""
    markup = _render(interactive=True)

    assert "Input (preview)" in markup
    assert "3 4" in markup
