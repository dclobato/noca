#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import cast

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from shared.services.testcase_view import TestCaseRowView
from shared.tc_zip import MAX_INLINE_TESTCASE_BYTES
from web.models.contest import Contest
from web.models.problem import Problem, ProblemLanguageLimit, ProblemTestCase
from web.services.problem_service import (
    build_export_zip,
    delete_md_statement,
    delete_testcase_files,
    get_statement_path,
    read_testcase_full,
    read_testcase_preview,
    renumber_testcase_files,
    save_md_statement,
    save_problem_statement,
    save_testcase_files,
)

logger = logging.getLogger(__name__)

LanguageLimitForm = dict[str, str | int]


# ---------------------------------------------------------------------------
# anyio thread-run closure factories
# ---------------------------------------------------------------------------


def _run_sync0[T](fn: Callable[[], T]) -> Callable[[], T]:
    return fn


def _save_problem_statement_for(problem_id: str, pdf_bytes: bytes, statement_dir: Path) -> Callable[[], None]:
    def _save() -> None:
        save_problem_statement(problem_id, pdf_bytes, statement_dir)

    return _save


def _save_md_statement_for(problem_id: str, md_text: str, statement_dir: Path) -> Callable[[], None]:
    def _save() -> None:
        save_md_statement(problem_id, md_text, statement_dir)

    return _save


def _delete_md_statement_for(problem_id: str, statement_dir: Path) -> Callable[[], None]:
    def _delete() -> None:
        delete_md_statement(problem_id, statement_dir)

    return _delete


def _delete_pdf_statement_for(problem_id: str, statement_dir: Path) -> Callable[[], None]:
    def _delete() -> None:
        get_statement_path(problem_id, statement_dir).unlink(missing_ok=True)

    return _delete


def _save_testcase_files_for(
    problem_id: str,
    ordinal: int,
    in_bytes: bytes,
    out_bytes: bytes,
    testcase_dir: Path,
) -> Callable[[], tuple[int, int]]:
    def _save() -> tuple[int, int]:
        return save_testcase_files(problem_id, ordinal, in_bytes, out_bytes, testcase_dir)

    return _save


def _delete_testcase_files_for(problem_id: str, ordinal: int, testcase_dir: Path) -> Callable[[], None]:
    def _delete() -> None:
        delete_testcase_files(problem_id, ordinal, testcase_dir)

    return _delete


def _renumber_testcase_files_for(
    problem_id: str,
    old_ordinal: int,
    new_ordinal: int,
    testcase_dir: Path,
) -> Callable[[], None]:
    def _renumber() -> None:
        renumber_testcase_files(problem_id, old_ordinal, new_ordinal, testcase_dir)

    return _renumber


def _read_testcase_preview_for(problem_id: str, ordinal: int, testcase_dir: Path) -> Callable[[], tuple[str, str]]:
    def _read() -> tuple[str, str]:
        return read_testcase_preview(problem_id, ordinal, testcase_dir)

    return _read


def _read_testcase_full_for(problem_id: str, ordinal: int, testcase_dir: Path) -> Callable[[], tuple[str, str]]:
    def _read() -> tuple[str, str]:
        return read_testcase_full(problem_id, ordinal, testcase_dir)

    return _read


def _build_export_zip_for(
    problem: Problem,
    testcase_dir: Path,
    statement_dir: Path,
    limits_map: dict[str, ProblemLanguageLimit],
) -> Callable[[], bytes]:
    def _build() -> bytes:
        return build_export_zip(problem, testcase_dir, statement_dir, limits_map)

    return _build


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


def _html(response: object) -> HTMLResponse:
    return cast(HTMLResponse, response)


def _redirect(url: str, status_code: int = 303) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=status_code)


# ---------------------------------------------------------------------------
# Contest-state helpers
# ---------------------------------------------------------------------------


def _is_edit_allowed(contest: Contest) -> bool:
    return contest.upcoming and contest.active


def _is_limits_edit_allowed(contest: Contest) -> bool:
    return contest.active and not contest.is_past


def _is_remove_allowed(contest: Contest) -> bool:
    return contest.upcoming and contest.active


def _remove_blocked_reason(contest: Contest) -> str | None:
    if _is_remove_allowed(contest):
        return None
    if contest.is_running:
        return "Contest is running — wait until it ends"
    if contest.is_past:
        return "Contest has ended"
    if not contest.active:
        return "Contest is inactive"
    return "Removal not allowed"


def build_testcase_row_views(
    request: Request,
    contest: Contest,
    test_cases: list[ProblemTestCase],
    previews: dict[str, tuple[str, str]],
) -> list[TestCaseRowView]:
    """Adapt Web ``ProblemTestCase`` rows into shared list-partial view models.

    URLs are pre-built with the slug-scoped Web route names so the shared
    template never resolves module-specific ``url_for`` names.
    """
    slug = contest.login_slug
    rows: list[TestCaseRowView] = []
    for tc in sorted(test_cases, key=lambda item: item.ordinal):
        in_size = tc.input_size_bytes or 0
        out_size = tc.output_size_bytes or 0
        in_preview, out_preview = previews.get(tc.id, ("", ""))
        rows.append(
            TestCaseRowView(
                id=tc.id,
                ordinal=tc.ordinal,
                is_sample=tc.is_sample,
                has_explanation=bool(tc.explanation),
                input_preview=in_preview,
                output_preview=out_preview,
                input_size_bytes=in_size,
                output_size_bytes=out_size,
                is_large=in_size > MAX_INLINE_TESTCASE_BYTES or out_size > MAX_INLINE_TESTCASE_BYTES,
                edit_url=str(request.url_for("edit_test_case_form", slug=slug, problem_id=tc.problem_id, tc_id=tc.id)),
                download_url=str(
                    request.url_for("download_test_case", slug=slug, problem_id=tc.problem_id, tc_id=tc.id)
                ),
                replace_url=str(request.url_for("replace_test_case", slug=slug, problem_id=tc.problem_id, tc_id=tc.id)),
                move_url=str(request.url_for("move_test_case_route", slug=slug, problem_id=tc.problem_id, tc_id=tc.id)),
                toggle_sample_url=str(
                    request.url_for("toggle_test_case_sample", slug=slug, problem_id=tc.problem_id, tc_id=tc.id)
                ),
            )
        )
    return rows


@lru_cache(maxsize=128)
def _label(ordinal: int) -> str:
    """Bijective base-26: 1→A, 2→B, ..., 26→Z, 27→AA"""
    result = ""
    n = ordinal
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result
