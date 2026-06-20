from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import contest_languages as contest_languages_table
from web.config import settings
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import ProblemLanguageLimit, ProblemTestCase
from web.services.problem_service import BALLOON_COLORS, import_problem_from_zip
from web.services.problem_service.importing import _pick_balloon_color


async def _make_language(session: AsyncSession, language_id: str, name: str) -> Language:
    language = Language(
        id=language_id,
        name=name,
        icon=language_id,
        compile_image=f"noca/{language_id}:compile",
        run_image=f"noca/{language_id}:run",
        compile_cmd=["true"],
        run_cmd=["true"],
        source_filename="source.txt",
        artifact_path="/sandbox/source.txt",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(language)
    await session.flush()
    return language


async def _make_contest(session: AsyncSession, *, created_by_uberadmin_id: str) -> Contest:
    contest = Contest(
        contest_name="Import Contest",
        contest_url="https://contest.example.com",
        login_slug="import-contest",
        created_by_uberadmin_id=created_by_uberadmin_id,
        start_time=datetime.now(UTC) + timedelta(days=1),
        duration_minutes=180,
        stop_answers_after=180,
        stop_updating_scoreboard=180,
        clarifications_timeout_minutes=20,
        tasks_timeout_minutes=20,
        review_timeout_minutes=20,
        max_problem_file_size_bytes=65536,
        wa_penalty=20,
        show_limits=True,
        autojudge_only=False,
        allow_print_requests=True,
        accept_pe=False,
        ce_adds_penalty=False,
        contest_timezone="UTC",
        active=True,
    )
    session.add(contest)
    await session.flush()
    return contest


def _problem_zip_bytes() -> bytes:
    payload = {
        "title": "Imported Problem",
        "time_limit_ms": 1000,
        "memory_limit_kb": 262144,
        "pids_limit": 64,
        "language_limits": {
            "python3": {
                "time_limit_ms": "1500",
                "memory_limit_kb": "262144",
                "pids_limit": "64",
                "output_limit_in_bytes": "",
                "repetitions": 2,
            },
            "java": {
                "time_limit_ms": "2000",
                "memory_limit_kb": "262144",
                "pids_limit": "64",
                "output_limit_in_bytes": "",
                "repetitions": 2,
            },
        },
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("problem.json", json.dumps(payload))
        archive.writestr("statement.md", "# Imported Problem\n\nNo external links here.\n")
        archive.writestr("in/001.in", "1 2\n")
        archive.writestr("out/001.out", "3\n")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_import_problem_from_zip_skips_disallowed_language_limits(
    session: AsyncSession,
    uberadmin,
) -> None:
    python = await _make_language(session, "python3", "Python 3")
    java = await _make_language(session, "java", "Java")
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.execute(
        insert(contest_languages_table),
        [{"contest_id": contest.id, "language_id": python.id}],
    )
    await session.commit()

    result = await import_problem_from_zip(
        session,
        contest,
        _problem_zip_bytes(),
        settings.PROBLEM_TESTCASE_DIR,
        settings.PROBLEM_STATEMENT_DIR,
    )

    persisted_limits = (
        (
            await session.execute(
                select(ProblemLanguageLimit).where(ProblemLanguageLimit.problem_id == result.problem.id)
            )
        )
        .scalars()
        .all()
    )

    assert result.skipped_language_ids == [java.id]
    assert [limit.language_id for limit in persisted_limits] == [python.id]


def _problem_zip_with_explanation() -> bytes:
    payload = {
        "title": "Explained Problem",
        "time_limit_ms": 1000,
        "memory_limit_kb": 262144,
        "pids_limit": 64,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("problem.json", json.dumps(payload))
        archive.writestr("statement.md", "# Explained\n\nNo external links here.\n")
        archive.writestr("in/001.in", "1 2\n")
        archive.writestr("out/001.out", "3\n")
        archive.writestr("explanation/001.txt", "1 + 2 = 3")
        archive.writestr("in/002.in", "4 5\n")
        archive.writestr("out/002.out", "9\n")  # second case has no explanation
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_import_problem_from_zip_persists_explanations(
    session: AsyncSession,
    uberadmin,
) -> None:
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.commit()

    result = await import_problem_from_zip(
        session,
        contest,
        _problem_zip_with_explanation(),
        settings.PROBLEM_TESTCASE_DIR,
        settings.PROBLEM_STATEMENT_DIR,
    )

    test_cases = (
        (
            await session.execute(
                select(ProblemTestCase)
                .where(ProblemTestCase.problem_id == result.problem.id)
                .order_by(ProblemTestCase.ordinal)
            )
        )
        .scalars()
        .all()
    )

    assert [tc.explanation for tc in test_cases] == ["1 + 2 = 3", None]


# ── Balloon color picker ──────────────────────────────────────────────────────


def test_pick_balloon_color_avoids_used_colors() -> None:
    used = set(BALLOON_COLORS[:-1])  # all but the last
    color = _pick_balloon_color(used)
    assert color == BALLOON_COLORS[-1]


def test_pick_balloon_color_falls_back_when_all_used() -> None:
    color = _pick_balloon_color(set(BALLOON_COLORS))
    assert color in BALLOON_COLORS


def test_pick_balloon_color_is_case_insensitive() -> None:
    used = {c.lower() for c in BALLOON_COLORS[:-1]}
    color = _pick_balloon_color(used)
    assert color == BALLOON_COLORS[-1]


@pytest.mark.asyncio
async def test_import_assigns_predefined_balloon_color(
    session: AsyncSession,
    uberadmin,
) -> None:
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.commit()

    result = await import_problem_from_zip(
        session,
        contest,
        _problem_zip_bytes(),
        settings.PROBLEM_TESTCASE_DIR,
        settings.PROBLEM_STATEMENT_DIR,
    )

    assert result.problem.color in BALLOON_COLORS
