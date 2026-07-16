#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Template tests for problem-detail sample testcase layout."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _sample_loop_tag(template: str) -> str:
    """Return the sample-test-case loop tag used by this module's template."""
    arena_tag = "{% for tc in sample_test_cases %}"
    if arena_tag in template:
        return arena_tag
    return "{% for ordinal, in_text, out_text, explanation in tc_contents %}"


def _sample_testcase_block(template_path: Path) -> str:
    """Return the sample testcase loop body from a problem-detail template."""
    template = template_path.read_text(encoding="utf-8")
    start = template.index(_sample_loop_tag(template))
    end = template.index("{% endfor %}", start)
    return template[start:end]


def test_arena_problem_detail_stacks_sample_input_above_output() -> None:
    """Arena sample testcase cards render input above output."""
    block = _sample_testcase_block(ROOT / "arena/template/problems/problem_detail.html")

    assert 'class="row g-2"' not in block
    assert 'class="col-md-6"' not in block
    assert block.index("Input") < block.index("Output")


def test_web_problem_detail_stacks_sample_input_above_output() -> None:
    """Web sample testcase cards render input above output."""
    block = _sample_testcase_block(ROOT / "web/template/contest/problem_detail.html")

    assert 'class="row g-3"' not in block
    assert 'class="col-md-6"' not in block
    assert block.index("Input") < block.index("Output")


def test_interactive_problems_render_interactions_instead_of_sample_test_cases() -> None:
    """An interactive problem's public examples are conversations, not test cases.

    The two are mutually exclusive branches of one ``has_custom_validator`` check,
    so an interactive problem structurally cannot reach the sample-test-case loop —
    and therefore cannot leak a secret case's input or a nonexistent expected output.
    """
    for template_path in (
        ROOT / "arena/template/problems/problem_detail.html",
        ROOT / "web/template/contest/problem_detail.html",
    ):
        template = template_path.read_text(encoding="utf-8")

        gate_at = template.index("{% if has_custom_validator %}")
        interactions_at = template.index("{% for interaction in sample_interactions %}")
        sample_loop_at = template.index(_sample_loop_tag(template))

        # The interactive branch comes first and renders conversations; the
        # sample-test-case loop lives past it, in the non-interactive branch.
        assert gate_at < interactions_at < sample_loop_at, template_path.name
        # With the branches split, no inner interactive gate is left to get wrong.
        assert "{% if not has_custom_validator %}" not in template, template_path.name
