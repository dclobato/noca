#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""What one test-case action asks for, as data the planner can apply.

Every action on the judgment-data pages -- replace this case, delete that one,
add these, reorder them, replace them all -- is expressed as a
:class:`PendingTestCaseOps` with exactly one field set, and applied immediately.
The type is shared so the planner
(:mod:`shared.services.testcase_save_plan`) has one shape to consume rather than
one per endpoint.

It briefly described something larger: a single Save that carried every mutation
at once, with a form parser, an uploads reader and rules refusing contradictory
combinations. Separate pages retired all of that -- with one action per request
there is nothing to combine -- and what remains is the description itself plus
the one parser still needed, for the rows an author types inline before saving
them.

Nothing here is decided from validator presence. The problem's stored
:class:`~shared.enumerations.ProblemValidatorType` decides whether a case needs
expected output or may be a sample, so a problem whose validator source was
removed still behaves as the interactive problem it is.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from shared.services.problem_package.testcase_archive import ParsedTestCases
from shared.tc_zip import SingleTestCase, inline_oversized_side


@dataclass(frozen=True, slots=True)
class CaseContent:
    """One test case's content, however it reached the Save.

    Attributes:
        input_bytes: The case input.
        output_bytes: The expected output, or ``None`` for an interactive case,
            which has none by construction.
        explanation: Author explanation, or ``None``.
        is_sample: Whether contestants see it. Always ``False`` for an
            interactive problem, whose examples are sample interactions.
        states_metadata: Whether ``is_sample`` and ``explanation`` were *stated*
            by whoever submitted this content, rather than merely accompanying
            it. An inline edit form states both -- it shows the checkbox and the
            explanation box, so clearing either is a decision -- while a
            single-case ZIP carries content only: it has no sample flag, and an
            archive without ``explanation.txt`` must leave the author's text
            alone rather than erase it. A replacement that does not state them
            keeps what the row already holds.
    """

    input_bytes: bytes
    output_bytes: bytes | None
    explanation: str | None = None
    is_sample: bool = False
    states_metadata: bool = False


@dataclass(frozen=True, slots=True)
class PendingTestCaseOps:
    """Everything one Save changes about a problem's test cases.

    Attributes:
        removals: Ids of existing cases to delete.
        sample_toggles: Ids of existing cases whose sample flag flips.
        added: New cases, from inline rows and single-case archives, in order.
        replacements: ``{test_case_id: replacement content}``.
        bulk_cases: The complete replacement set from a replace-all archive, or
            ``None`` when the Save carries none. An *empty tuple* is a legitimate
            "replace everything with nothing" and is distinct from ``None``.
        order: The ids of every surviving case, in the order they should end up,
            or ``None`` to keep the stored order. This is how a drag-reorder
            reaches the staged swap: permuting files is a filesystem change like
            any other, and doing it outside the swap was the one action that wrote
            before it committed.
    """

    removals: frozenset[str] = frozenset()
    sample_toggles: frozenset[str] = frozenset()
    added: tuple[CaseContent, ...] = ()
    replacements: Mapping[str, CaseContent] = field(default_factory=dict)
    bulk_cases: tuple[CaseContent, ...] | None = None
    order: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class SubmittedCaseRow:
    """One inline add-row retained after a rejected submission.

    Attributes:
        index: Stable form-field suffix used by the browser row.
        input_text: Submitted input text, unchanged for re-rendering.
        output_text: Submitted expected output, unchanged for re-rendering.
        explanation: Submitted explanation, unchanged for re-rendering.
        is_sample: Whether the submitted row was marked as a sample.
        error: Actionable validation error for this row, when present.
        error_field: Which textarea receives invalid styling and focus.
    """

    index: int
    input_text: str
    output_text: str
    explanation: str
    is_sample: bool
    error: str = ""
    error_field: Literal["", "input", "output"] = ""

    @property
    def has_content(self) -> bool:
        """Return whether the row describes a case rather than an empty draft."""
        return bool(self.input_text or self.output_text)

    def to_content(self, *, interactive: bool) -> CaseContent:
        """Convert the submitted text to the planner's content shape."""
        return CaseContent(
            input_bytes=self.input_text.encode(),
            output_bytes=None if interactive else self.output_text.encode(),
            explanation=self.explanation.strip() or None,
            is_sample=not interactive and self.is_sample,
        )


def case_content_from_single_archive(
    single: SingleTestCase,
    *,
    interactive: bool,
    is_sample: bool = False,
) -> CaseContent:
    """Adapt one parsed single-case archive to the planner's content shape."""
    return CaseContent(
        input_bytes=single.input_bytes,
        output_bytes=None if interactive else single.output_bytes,
        explanation=single.explanation,
        is_sample=is_sample and not interactive,
    )


def case_contents_from_bulk_archive(parsed: ParsedTestCases, *, interactive: bool) -> tuple[CaseContent, ...]:
    """Adapt a parsed bulk archive to ordered planner content."""
    return tuple(
        CaseContent(
            input_bytes=input_bytes,
            output_bytes=None if interactive else output_bytes,
            explanation=parsed.explanations.get(source_ordinal),
        )
        for source_ordinal, (input_bytes, output_bytes) in sorted(parsed.pairs.items())
    )


def _inline_indices(form: Mapping[str, Any]) -> list[int]:
    """Return the sorted indices of the inline add-rows present in ``form``."""
    return sorted(
        {
            int(key.rsplit("_", 1)[1])
            for key in form
            if key.startswith(("tc_in_", "tc_out_", "tc_explanation_", "tc_is_sample_"))
            and key.rsplit("_", 1)[1].isdigit()
        }
    )


def submitted_case_rows(form: Mapping[str, Any]) -> tuple[SubmittedCaseRow, ...]:
    """Return every submitted inline row, including blank drafts.

    Retaining blank rows matters after a rejected request: the browser created
    the row because the author intended to fill it, and silently removing it
    makes the error response look like the form was never submitted.
    """
    return tuple(
        SubmittedCaseRow(
            index=index,
            input_text=str(form.get(f"tc_in_{index}", "")),
            output_text=str(form.get(f"tc_out_{index}", "")),
            explanation=str(form.get(f"tc_explanation_{index}", "")),
            is_sample=bool(form.get(f"tc_is_sample_{index}")),
        )
        for index in _inline_indices(form)
    )


def first_oversized_submitted_case(
    rows: tuple[SubmittedCaseRow, ...],
    *,
    interactive: bool,
) -> tuple[int, Literal["input", "output"]] | None:
    """Return the first row and normalized side over the inline-edit limit."""
    for row in rows:
        if not row.has_content:
            continue
        content = row.to_content(interactive=interactive)
        oversized = inline_oversized_side(content.input_bytes, content.output_bytes)
        if oversized is not None:
            return row.index, oversized
    return None


def parse_inline_added_cases(form: Mapping[str, Any], *, interactive: bool) -> tuple[CaseContent, ...]:
    """Return the filled inline rows as case content, in submission order.

    The judgment-data pages' own Save is the only thing that collects typed rows:
    everything else on those pages posts immediately.
    """
    return tuple(row.to_content(interactive=interactive) for row in submitted_case_rows(form) if row.has_content)
