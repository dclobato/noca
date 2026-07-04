#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Template tests for problem-detail sample testcase layout."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _sample_testcase_block(template_path: Path) -> str:
    """Return the sample testcase loop body from a problem-detail template."""
    template = template_path.read_text(encoding="utf-8")
    if "{% for tc in sample_test_cases %}" in template:
        start = template.index("{% for tc in sample_test_cases %}")
    else:
        start = template.index("{% for ordinal, in_text, out_text, explanation in tc_contents %}")
    end = template.index("{% if", start)
    return template[start:end]


def test_arena_problem_detail_stacks_sample_input_above_output() -> None:
    """Arena sample testcase cards render input above output."""
    block = _sample_testcase_block(ROOT / "arena/template/problems/problem_detail.html")

    assert 'class="row g-2"' not in block
    assert 'class="col-md-6"' not in block
    assert '<div class="mb-3">' in block
    assert block.index("Input") < block.index("Output")


def test_web_problem_detail_stacks_sample_input_above_output() -> None:
    """Web sample testcase cards render input above output."""
    block = _sample_testcase_block(ROOT / "web/template/contest/problem_detail.html")

    assert 'class="row g-3"' not in block
    assert 'class="col-md-6"' not in block
    assert '<div class="mb-3">' in block
    assert block.index("Input") < block.index("Output")
