#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Presentation model for the judgment-data editor, shared by both modules.

A problem is edited through two doors. The *definition* editor holds what the
problem is -- title, statement, limits -- and saves it in one form. The
*judgment-data* editor holds what judging runs against: test cases, the custom
validator, and (for an interactive problem) its sample interactions.

They are separate because they are used differently. A statement is written once;
test cases are iterated on, are numerous, and can be large. So judgment data is a
set of **pages** rather than panes of one form, and each operation on existing
data posts immediately instead of waiting for a Save. Nothing is held in the
browser that the server has not seen.

Which pages exist follows the problem's stored strategy, and the shared partials
resolve no module route names: each module hands over URLs it built itself, the
same arrangement :class:`shared.services.testcase_view.TestCaseRowView` uses for
the per-row table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from shared.enumerations import ProblemValidatorType
from shared.services.custom_validator import ValidatorStatusView
from shared.services.problem_editor_header import ProblemEditorHeaderView
from shared.services.problem_judgeability import ProblemJudgeabilityFacts, judgeability_error

#: Canonical judgment page keys, in navigation order.
PAGE_TEST_CASES = "test-cases"
PAGE_VALIDATOR = "validator"
PAGE_INTERACTIONS = "interactions"

JudgmentReadinessState = Literal["ready", "pending", "incomplete"]


@dataclass(frozen=True)
class JudgmentNavItem:
    """One entry in the judgment-data navigation.

    Attributes:
        key: The canonical page key.
        label: What the operator reads.
        url: Where it goes.
        badge: A short state note -- "no cases", "no validator" -- or empty. The
            nav doubles as the judgeability hint, so an author can see at a glance
            what is still missing before the problem can judge anything.
    """

    key: str
    label: str
    url: str
    badge: str = ""


@dataclass(frozen=True)
class JudgmentReadinessView:
    """The consolidated judgeability state shown above every judgment page.

    Attributes:
        state: Semantic state used by the shared presentation.
        label: Short, decisive status text.
        detail: The evidence or recovery step behind the status.
        has_submissions: Whether existing verdicts may need refreshing after an
            author changes judgment data.
        rejudge_url: Optional permission-checked action exposed by the module.
        rejudge_return_path: Safe local path the action returns to.
    """

    state: JudgmentReadinessState
    label: str
    detail: str
    has_submissions: bool = False
    rejudge_url: str = ""
    rejudge_return_path: str = ""


@dataclass(frozen=True)
class JudgmentShellView:
    """Everything the judgment-data chrome needs, for either module.

    Attributes:
        header: The chrome both editor doors share -- title, action bar, badge.
        problem_label: How the problem is named in flash and modal copy.
        validator_type: The stored strategy itself, for the pages' own branching.
        is_interactive: Whether that strategy is interactive.
        is_edit_allowed: Whether mutations are offered at all. A Contest that has
            started renders these pages read-only.
        read_only_reason: Why editing is unavailable, when it is.
        active_page: The page being rendered.
        pages: The navigation, already filtered by strategy.
        readiness: Consolidated judgeability and follow-up state.
        definition_url: Back to the definition editor.
        problem_list_url: Back to the problem list.
    """

    header: ProblemEditorHeaderView
    problem_label: str
    validator_type: ProblemValidatorType
    is_interactive: bool
    is_edit_allowed: bool
    active_page: str
    pages: tuple[JudgmentNavItem, ...]
    readiness: JudgmentReadinessView
    definition_url: str
    problem_list_url: str
    read_only_reason: str = ""


@dataclass(frozen=True)
class TestCasesPageView:
    """Action URLs for the test-cases page.

    Attributes:
        bulk_url: Replace every case from one archive.
        upload_url: Append cases from single-case archives.
        add_url: Save the rows typed inline on this page.
        case_count: How many cases the problem currently has, for the
            replace-all confirmation.
    """

    bulk_url: str
    upload_url: str
    add_url: str
    case_count: int = 0


@dataclass(frozen=True)
class ValidatorPageView:
    """Action URLs and state for the validator page.

    Attributes:
        upload_url: Stage a candidate revision.
        remove_url: Remove both revisions.
        download_url: Download the current source, when one exists.
        source_url: View the current source, when one exists.
        status_template: The module's own status-badge partial.
        interaction_count: Interactions the removal modal must account for.
    """

    upload_url: str
    remove_url: str
    download_url: str | None = None
    source_url: str | None = None
    status_template: str | None = None
    interaction_count: int = 0


@dataclass(frozen=True)
class InteractionsPageView:
    """Action URLs for the sample-interactions page.

    Attributes:
        add_url: Save the transcripts typed inline on this page.
        max_interactions: The cap, so the page can stop offering rows.
        interaction_count: How many the problem currently has.
    """

    add_url: str
    max_interactions: int
    interaction_count: int = 0


def build_judgment_readiness(
    *,
    facts: ProblemJudgeabilityFacts,
    validator_status: ValidatorStatusView,
    has_submissions: bool = False,
    rejudge_url: str = "",
    rejudge_return_path: str = "",
) -> JudgmentReadinessView:
    """Build one actionable summary of whether the problem can be judged.

    Args:
        facts: Canonical cross-domain judgeability facts.
        validator_status: Current custom-validator lifecycle state.
        has_submissions: Whether submissions already exist for this problem.
        rejudge_url: Optional module-owned full-rejudge action.
        rejudge_return_path: Safe local return path for that action.

    Returns:
        JudgmentReadinessView: Status copy and optional follow-up action.
    """
    error = judgeability_error(facts)
    interactive = facts.strategy is ProblemValidatorType.INTERACTIVE
    if error is not None:
        recovery: list[str] = []
        if facts.strategy is ProblemValidatorType.STANDARD:
            if facts.total_case_count == 0:
                recovery.append("Add at least one test case.")
            elif facts.cases_missing_expected_output > 0:
                recovery.append("Replace every test case that has no expected output.")
        elif interactive:
            if not facts.has_active_valid_validator:
                recovery.append(
                    "Validator compilation is still in progress."
                    if validator_status.polling
                    else "Upload and validate an interactive validator."
                )
            if facts.secret_case_count == 0:
                recovery.append("Add at least one secret test case.")
        else:
            recovery.append(error)

        if interactive and validator_status.polling and len(recovery) == 1:
            return JudgmentReadinessView(
                state="pending",
                label="Validator compiling",
                detail=recovery[0],
                has_submissions=has_submissions,
                rejudge_url=rejudge_url,
                rejudge_return_path=rejudge_return_path,
            )
        return JudgmentReadinessView(
            state="incomplete",
            label="Judgment data incomplete",
            detail=" ".join(recovery),
            has_submissions=has_submissions,
            rejudge_url=rejudge_url,
            rejudge_return_path=rejudge_return_path,
        )

    if interactive:
        detail = (
            "The active validator is ready while a replacement compiles."
            if validator_status.polling
            else (
                f"{facts.total_case_count} test case"
                f"{'s are' if facts.total_case_count != 1 else ' is'} configured with an active validator."
            )
        )
    else:
        detail = f"{facts.total_case_count} test case{'s are' if facts.total_case_count != 1 else ' is'} configured."
    return JudgmentReadinessView(
        state="ready",
        label="Ready to judge",
        detail=detail,
        has_submissions=has_submissions,
        rejudge_url=rejudge_url,
        rejudge_return_path=rejudge_return_path,
    )


def build_judgment_nav(
    *,
    validator_type: ProblemValidatorType,
    urls: dict[str, str],
    badges: dict[str, str] | None = None,
) -> tuple[JudgmentNavItem, ...]:
    """Return the pages a problem of this strategy actually has.

    A standard problem has no validator and no sample interactions, so offering
    those pages would invite an author to configure something that can never run.

    Args:
        validator_type: The problem's stored strategy.
        urls: ``{page key: url}`` for every page this module renders.
        badges: Optional ``{page key: short state note}``.

    Returns:
        tuple[JudgmentNavItem, ...]: The navigation, in display order.
    """
    labels = {
        PAGE_TEST_CASES: "Test cases",
        PAGE_VALIDATOR: "Validator",
        PAGE_INTERACTIONS: "Sample interactions",
    }
    interactive = validator_type is ProblemValidatorType.INTERACTIVE
    keys = [PAGE_TEST_CASES] + ([PAGE_VALIDATOR, PAGE_INTERACTIONS] if interactive else [])
    notes = badges or {}
    return tuple(
        JudgmentNavItem(key=key, label=labels[key], url=urls[key], badge=notes.get(key, ""))
        for key in keys
        if key in urls
    )
