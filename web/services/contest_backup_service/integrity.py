#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Fail-closed reference-graph validation for backup metadata.

This module composes the generic row/identifier primitives in
:mod:`web.services.contest_backup_service.row_validation` into the backup-specific
checks: contest scoping, foreign-key resolution across the whole archive graph,
and manifest-to-payload consistency, all before any row is written.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from shared.db_schema import (
    clarifications,
    contests,
    human_submission_confirmations,
    problem_custom_validators,
    problem_language_limits,
    problem_sample_interactions,
    problems,
    sites,
    submission_interactive_attempts,
    submission_judgment_audit,
    submission_judgments,
    submission_test_results,
    submissions,
    tasks,
    test_cases,
    users,
    users_media,
    verdict_overrides,
)
from shared.enumerations import ALL_CONTEST_ROLES, ProblemValidatorType

from .models import (
    ContestBackupError,
)
from .row_validation import (
    as_mapping,
    index_rows,
    positive_int,
    require_exact_keys,
    require_member,
    require_reference,
    require_unique_strings,
    required_id,
    row_objects,
    validate_child,
    validate_optional_reference,
    validate_optional_user_reference,
    validate_row,
)
from .validation import ArchiveIndex

_PROBLEM_ENTRY_KEYS = {
    "problem",
    "dir",
    "test_cases",
    "language_limits",
    "sample_interactions",
    "custom_validator",
    "categories",
}
_JUDGMENT_ENTRY_KEYS = {
    "judgment",
    "test_results",
    "confirmations",
    "overrides",
    "interactive_attempts",
    "audit",
}


def validate_backup_integrity(
    *,
    manifest: dict[str, Any],
    problem_entries: list[dict[str, Any]],
    user_rows: list[dict[str, Any]],
    media_rows: list[dict[str, Any]],
    submission_rows: list[dict[str, Any]],
    judgment_entries: list[dict[str, Any]],
    clarification_rows: list[dict[str, Any]],
    task_rows: list[dict[str, Any]],
    archive_index: ArchiveIndex,
) -> None:
    """Validate every row, identifier, reference, and required payload file."""
    contest_row = validate_row(contests, manifest["contest"], "manifest contest")
    contest_id = required_id(contest_row, "manifest contest")
    include_hashes = manifest["includes"]["include_password_hashes"]

    site_by_id = index_rows(sites, manifest["sites"], "site")
    user_by_id = index_rows(
        users,
        user_rows,
        "user",
        optional_columns=set() if include_hashes else {"password_hash"},
    )
    problem_by_id, test_case_by_id = _validate_problems(
        problem_entries,
        manifest["problems"],
        contest_id,
        archive_index,
    )
    submission_by_id = index_rows(submissions, submission_rows, "submission")

    require_unique_strings(manifest["language_ids"], "contest language id")
    _validate_contest_scopes(contest_id, site_by_id.values(), user_by_id.values(), problem_by_id.values())
    _validate_user_rows(user_by_id.values(), site_by_id, include_hashes)
    validate_optional_user_reference(contest_row.get("owner_user_id"), user_by_id, "contest owner")
    validate_optional_user_reference(contest_row.get("chief_judge_id"), user_by_id, "chief judge")
    owner_id = contest_row.get("owner_user_id")
    chief_id = contest_row.get("chief_judge_id")
    if owner_id is not None and user_by_id[owner_id]["role"] != "ADMIN":
        raise ContestBackupError("Contest owner must reference an ADMIN user.")
    if chief_id is not None and user_by_id[chief_id]["role"] != "JUDGE":
        raise ContestBackupError("Chief judge must reference a JUDGE user.")

    for row in media_rows:
        validated = validate_row(users_media, row, "user media")
        require_reference(validated.get("user_id"), user_by_id, "user media user")

    submission_problem: dict[str, str] = {}
    for submission_id, row in submission_by_id.items():
        problem_id = require_reference(row.get("problem_id"), problem_by_id, "submission problem")
        require_reference(row.get("team_id"), user_by_id, "submission team")
        submission_problem[submission_id] = problem_id

    _validate_judgments(
        judgment_entries,
        submission_by_id,
        submission_problem,
        test_case_by_id,
        user_by_id,
    )
    _validate_clarifications(clarification_rows, problem_by_id, user_by_id)
    _validate_tasks(task_rows, problem_by_id, user_by_id)


def _validate_problems(
    entries: list[dict[str, Any]],
    references: list[dict[str, Any]],
    contest_id: str,
    archive_index: ArchiveIndex,
) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[dict[str, Any], str]]]:
    problem_by_id: dict[str, dict[str, Any]] = {}
    test_case_by_id: dict[str, tuple[dict[str, Any], str]] = {}
    seen_ordinals: set[int] = set()
    actual_references: set[tuple[str, int, str]] = set()

    for position, entry in enumerate(entries):
        require_exact_keys(entry, _PROBLEM_ENTRY_KEYS, f"problem entry {position}")
        # No optional columns: the single supported version states every column
        # the live table has, so a row missing one is malformed rather than old.
        problem = validate_row(problems, as_mapping(entry["problem"], "problem row"), "problem")
        problem_id = required_id(problem, "problem")
        if problem_id in problem_by_id:
            raise ContestBackupError(f"Duplicate problem id: {problem_id!r}.")
        ordinal = positive_int(problem.get("ordinal"), "problem ordinal")
        if ordinal in seen_ordinals:
            raise ContestBackupError(f"Duplicate problem ordinal: {ordinal}.")
        if problem.get("contest_id") != contest_id:
            raise ContestBackupError(f"Problem {problem_id!r} belongs to a different contest.")
        directory = entry["dir"]
        expected_directory = f"problems/{ordinal:03d}"
        if directory != expected_directory:
            raise ContestBackupError(f"Problem {problem_id!r} must use directory {expected_directory!r}.")

        validator = entry["custom_validator"]
        validated_validator = None
        if validator is not None:
            validated_validator = validate_row(
                problem_custom_validators,
                as_mapping(validator, "custom validator"),
                "custom validator",
            )
            if validated_validator.get("problem_id") != problem_id:
                raise ContestBackupError(f"Custom validator for {problem_id!r} has a mismatched problem id.")

        # NOT NULL in the live table, so validate_row has already refused a row
        # that omits it or states a value outside the enum.
        strategy = ProblemValidatorType(problem["validator_type"])
        _validate_problem_children(entry, problem_id, directory, strategy, archive_index, test_case_by_id)
        problem_by_id[problem_id] = problem
        seen_ordinals.add(ordinal)
        actual_references.add((problem_id, ordinal, directory))

    declared_references = {
        (reference["original_id"], reference["ordinal"], reference["dir"]) for reference in references
    }
    if actual_references != declared_references:
        raise ContestBackupError("Manifest problem references do not match problems.json.")
    return problem_by_id, test_case_by_id


def _validate_problem_children(
    entry: dict[str, Any],
    problem_id: str,
    directory: str,
    strategy: ProblemValidatorType,
    archive_index: ArchiveIndex,
    test_case_by_id: dict[str, tuple[dict[str, Any], str]],
) -> None:
    statement_names = {
        name for name in (f"{directory}/statement.md", f"{directory}/statement.pdf") if name in archive_index
    }
    if len(statement_names) != 1:
        raise ContestBackupError(f"Problem {problem_id!r} must contain exactly one statement file.")

    test_case_ordinals: set[int] = set()
    for row in row_objects(entry["test_cases"], "test_cases"):
        test_case = validate_row(test_cases, row, "test case")
        test_case_id = required_id(test_case, "test case")
        if test_case_id in test_case_by_id:
            raise ContestBackupError(f"Duplicate test-case id: {test_case_id!r}.")
        if test_case.get("problem_id") != problem_id:
            raise ContestBackupError(f"Test case {test_case_id!r} has a mismatched problem id.")
        ordinal = positive_int(test_case.get("ordinal"), "test-case ordinal")
        if ordinal in test_case_ordinals:
            raise ContestBackupError(f"Duplicate test-case ordinal {ordinal} for problem {problem_id!r}.")
        require_member(archive_index, f"{directory}/in/{ordinal:03d}.in")
        # Expected output is required by the *strategy*, not by whether a
        # validator row happens to hold active source: an interactive problem
        # whose source was removed still has input-only cases.
        if strategy is ProblemValidatorType.STANDARD:
            require_member(archive_index, f"{directory}/out/{ordinal:03d}.out")
        test_case_by_id[test_case_id] = (test_case, problem_id)
        test_case_ordinals.add(ordinal)

    for row in row_objects(entry["language_limits"], "language_limits"):
        limit = validate_row(problem_language_limits, row, "problem language limit")
        if limit.get("problem_id") != problem_id:
            raise ContestBackupError("Problem language limit has a mismatched problem id.")
    for row in row_objects(entry["sample_interactions"], "sample_interactions"):
        interaction = validate_row(problem_sample_interactions, row, "sample interaction")
        if interaction.get("problem_id") != problem_id:
            raise ContestBackupError("Sample interaction has a mismatched problem id.")

    categories = entry["categories"]
    if not isinstance(categories, list) or any(not isinstance(name, str) or not name.strip() for name in categories):
        raise ContestBackupError("Problem categories must be non-empty strings.")
    if len({name.casefold() for name in categories}) != len(categories):
        raise ContestBackupError("Problem categories contain duplicates.")


def _validate_judgments(
    entries: list[dict[str, Any]],
    submission_by_id: Mapping[str, dict[str, Any]],
    submission_problem: Mapping[str, str],
    test_case_by_id: Mapping[str, tuple[dict[str, Any], str]],
    user_by_id: Mapping[str, dict[str, Any]],
) -> None:
    judgment_ids: set[str] = set()
    for position, entry in enumerate(entries):
        require_exact_keys(entry, _JUDGMENT_ENTRY_KEYS, f"judgment entry {position}")
        judgment = validate_row(
            submission_judgments,
            as_mapping(entry["judgment"], "judgment row"),
            "judgment",
        )
        judgment_id = required_id(judgment, "judgment")
        if judgment_id in judgment_ids:
            raise ContestBackupError(f"Duplicate judgment id: {judgment_id!r}.")
        submission_id = require_reference(judgment.get("submission_id"), submission_by_id, "judgment submission")
        judgment_ids.add(judgment_id)

        for row in row_objects(entry["test_results"], "test_results"):
            child = validate_child(submission_test_results, row, judgment_id, "test result")
            test_case_id = require_reference(child.get("test_case_id"), test_case_by_id, "test result case")
            if test_case_by_id[test_case_id][1] != submission_problem[submission_id]:
                raise ContestBackupError("Test result references a case from a different problem.")
        for row in row_objects(entry["confirmations"], "confirmations"):
            child = validate_child(human_submission_confirmations, row, judgment_id, "confirmation")
            require_reference(child.get("judge_id"), user_by_id, "confirmation judge")
        for row in row_objects(entry["overrides"], "overrides"):
            child = validate_child(verdict_overrides, row, judgment_id, "verdict override")
            if child.get("submission_id") != submission_id:
                raise ContestBackupError("Verdict override has a mismatched submission id.")
            require_reference(child.get("overridden_by"), user_by_id, "override actor")
        for row in row_objects(entry["interactive_attempts"], "interactive_attempts"):
            validate_child(submission_interactive_attempts, row, judgment_id, "interactive attempt")
        for row in row_objects(entry["audit"], "audit"):
            child = validate_child(submission_judgment_audit, row, judgment_id, "judgment audit")
            if child.get("submission_id") != submission_id:
                raise ContestBackupError("Judgment audit has a mismatched submission id.")
            validate_optional_user_reference(child.get("actor_user_id"), user_by_id, "judgment audit actor")


def _validate_clarifications(
    rows: list[dict[str, Any]],
    problem_by_id: Mapping[str, dict[str, Any]],
    user_by_id: Mapping[str, dict[str, Any]],
) -> None:
    for row in rows:
        clarification = validate_row(clarifications, row, "clarification")
        validate_optional_reference(clarification.get("problem_id"), problem_by_id, "clarification problem")
        require_reference(clarification.get("team_id"), user_by_id, "clarification team")
        for column in ("judge_id", "hidden_by_judge_id", "hidden_by_admin_id"):
            validate_optional_user_reference(clarification.get(column), user_by_id, f"clarification {column}")


def _validate_tasks(
    rows: list[dict[str, Any]],
    problem_by_id: Mapping[str, dict[str, Any]],
    user_by_id: Mapping[str, dict[str, Any]],
) -> None:
    for row in rows:
        task = validate_row(tasks, row, "task")
        require_reference(task.get("team_id"), user_by_id, "task team")
        validate_optional_user_reference(task.get("staff_id"), user_by_id, "task staff")
        problem_id = task.get("problem_id")
        if problem_id is not None:
            require_reference(problem_id, problem_by_id, "task problem")


def _validate_contest_scopes(
    contest_id: str,
    site_rows: Iterable[dict[str, Any]],
    user_rows: Iterable[dict[str, Any]],
    problem_rows: Iterable[dict[str, Any]],
) -> None:
    for label, rows in (("site", site_rows), ("user", user_rows), ("problem", problem_rows)):
        if any(row.get("contest_id") != contest_id for row in rows):
            raise ContestBackupError(f"A {label} row belongs to a different contest.")


def _validate_user_rows(
    rows: Iterable[dict[str, Any]], site_by_id: Mapping[str, dict[str, Any]], include_hashes: bool
) -> None:
    roles = {role.value for role in ALL_CONTEST_ROLES}
    for row in rows:
        if row.get("role") not in roles:
            raise ContestBackupError(f"User {row.get('id')!r} has an invalid contest role.")
        validate_optional_reference(row.get("site_id"), site_by_id, "user site")
        if include_hashes and not row.get("password_hash"):
            raise ContestBackupError("Password hashes were requested, but a user hash is missing.")
        if not include_hashes and "password_hash" in row:
            raise ContestBackupError("users.json contains password hashes while the manifest says they are excluded.")
