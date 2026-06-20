from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import contest_languages as contest_languages_table
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem, ProblemLanguageLimit
from web.services.contest_service import (
    ContestMetadataInput,
    _validate_contest_metadata_fields,
    deactivate_past_contest,
    get_active_contests_grouped,
    get_contest_language_ids,
    get_inactive_contests,
    update_contest_metadata,
    validate_contest_metadata_update,
)


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


async def _make_upcoming_contest(session: AsyncSession, *, created_by_uberadmin_id: str) -> Contest:
    contest = Contest(
        contest_name="Upcoming Contest",
        contest_url="https://contest.example.com",
        login_slug="upcoming-contest",
        created_by_uberadmin_id=created_by_uberadmin_id,
        start_time=datetime.now(UTC) + timedelta(days=2),
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


def _metadata_input(**overrides: object) -> ContestMetadataInput:
    future_start = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    base: dict[str, object] = {
        "contest_url": "https://example.com",
        "start_time": future_start,
        "contest_timezone": "America/Sao_Paulo",
        "duration_minutes": 300,
        "stop_answers_after": 250,
        "stop_updating_scoreboard": 280,
        "clarifications_timeout_minutes": 30,
        "tasks_timeout_minutes": 30,
        "review_timeout_minutes": 30,
        "max_problem_file_size_bytes": 65536,
        "wa_penalty": 20,
        "show_limits": True,
        "autojudge_only": False,
        "allow_print_requests": True,
        "accept_pe": False,
        "ce_adds_penalty": False,
    }
    base.update(overrides)
    return ContestMetadataInput(**base)


def test_validate_contest_metadata_rejects_tasks_timeout_at_duration() -> None:
    metadata = _metadata_input(
        duration_minutes=60,
        stop_answers_after=40,
        stop_updating_scoreboard=50,
        tasks_timeout_minutes=60,
    )

    validated, errors = _validate_contest_metadata_fields(metadata)

    assert validated is None
    assert "Tasks timeout must be less than duration." in errors


def test_validate_contest_metadata_accepts_tasks_timeout_below_duration() -> None:
    metadata = _metadata_input(
        duration_minutes=60,
        stop_answers_after=50,
        stop_updating_scoreboard=40,
        tasks_timeout_minutes=59,
    )

    validated, errors = _validate_contest_metadata_fields(metadata)

    assert errors == []
    assert validated is not None


def _build_past_contest(*, allow_print_requests: bool) -> Contest:
    contest = Contest(
        contest_name="Contest",
        contest_url="https://original.example.com",
        login_slug="contest",
        created_by_uberadmin_id="ua-id",
        start_time=datetime.now(UTC) - timedelta(hours=3),
        duration_minutes=60,
        stop_answers_after=50,
        stop_updating_scoreboard=40,
        clarifications_timeout_minutes=20,
        tasks_timeout_minutes=20,
        review_timeout_minutes=20,
        max_problem_file_size_bytes=65536,
        wa_penalty=20,
        show_limits=True,
        autojudge_only=False,
        allow_print_requests=allow_print_requests,
        accept_pe=False,
        ce_adds_penalty=False,
        contest_timezone="UTC",
    )
    return contest


def test_validate_contest_metadata_update_allows_print_toggle_when_locked() -> None:
    contest = _build_past_contest(allow_print_requests=True)
    locked_start = contest.local_start_time.strftime("%Y-%m-%dT%H:%M")
    metadata = _metadata_input(
        start_time=locked_start,
        duration_minutes=60,
        stop_answers_after=50,
        stop_updating_scoreboard=40,
        clarifications_timeout_minutes=20,
        tasks_timeout_minutes=20,
        review_timeout_minutes=20,
        allow_print_requests=False,
        contest_url="https://changed.example.com",
        show_limits=False,
    )

    result = validate_contest_metadata_update(contest, metadata=metadata)

    assert result.success is True
    assert contest.allow_print_requests is False
    assert contest.contest_url == "https://original.example.com"
    assert contest.show_limits is True


def test_validate_contest_metadata_update_rejects_duration_ending_in_past_for_running_contest() -> None:
    contest = Contest(
        contest_name="Contest",
        contest_url="https://original.example.com",
        login_slug="contest",
        created_by_uberadmin_id="ua-id",
        start_time=datetime.now(UTC) - timedelta(hours=2),
        duration_minutes=180,
        stop_answers_after=150,
        stop_updating_scoreboard=140,
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
    )
    locked_start = contest.local_start_time.strftime("%Y-%m-%dT%H:%M")
    metadata = _metadata_input(
        start_time=locked_start,
        duration_minutes=60,
        stop_answers_after=50,
        stop_updating_scoreboard=40,
        clarifications_timeout_minutes=20,
        tasks_timeout_minutes=20,
        review_timeout_minutes=20,
    )

    result = validate_contest_metadata_update(contest, metadata=metadata)

    assert result.success is False
    assert "Duration cannot make the contest end in the past." in result.errors


def test_validate_contest_metadata_update_allows_duration_keeping_running_contest_active() -> None:
    contest = Contest(
        contest_name="Contest",
        contest_url="https://original.example.com",
        login_slug="contest",
        created_by_uberadmin_id="ua-id",
        start_time=datetime.now(UTC) - timedelta(hours=1),
        duration_minutes=180,
        stop_answers_after=110,
        stop_updating_scoreboard=100,
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
    )
    locked_start = contest.local_start_time.strftime("%Y-%m-%dT%H:%M")
    metadata = _metadata_input(
        start_time=locked_start,
        duration_minutes=90,
        stop_answers_after=80,
        stop_updating_scoreboard=70,
        clarifications_timeout_minutes=20,
        tasks_timeout_minutes=20,
        review_timeout_minutes=20,
    )

    result = validate_contest_metadata_update(contest, metadata=metadata)

    assert result.success is True
    assert contest.duration_minutes == 90


def test_validate_contest_metadata_update_rejects_changed_duration_still_ending_in_past() -> None:
    contest = _build_past_contest(allow_print_requests=True)
    locked_start = contest.local_start_time.strftime("%Y-%m-%dT%H:%M")
    metadata = _metadata_input(
        start_time=locked_start,
        duration_minutes=90,
        stop_answers_after=50,
        stop_updating_scoreboard=40,
        clarifications_timeout_minutes=20,
        tasks_timeout_minutes=20,
        review_timeout_minutes=20,
    )

    result = validate_contest_metadata_update(contest, metadata=metadata)

    assert result.success is False
    assert "Duration cannot make the contest end in the past." in result.errors


@pytest.mark.asyncio
async def test_update_contest_metadata_syncs_languages_and_removes_stale_problem_limits(
    session: AsyncSession,
    uberadmin,
) -> None:
    python = await _make_language(session, "python3", "Python 3")
    java = await _make_language(session, "java", "Java")
    cpp = await _make_language(session, "gcc-cpp23", "C++")
    contest = await _make_upcoming_contest(session, created_by_uberadmin_id=uberadmin.id)
    problem = Problem(
        contest_id=contest.id,
        title="Problem A",
        ordinal=1,
        color="#000000",
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
    )
    session.add(problem)
    await session.flush()
    await session.execute(
        insert(contest_languages_table),
        [
            {"contest_id": contest.id, "language_id": python.id},
            {"contest_id": contest.id, "language_id": java.id},
        ],
    )
    session.add(
        ProblemLanguageLimit(
            problem_id=problem.id,
            language_id=python.id,
            time_limit_ms=1200,
            memory_limit_kb=262144,
            pids_limit=64,
            output_limit_in_bytes=None,
            repetitions=2,
        )
    )
    session.add(
        ProblemLanguageLimit(
            problem_id=problem.id,
            language_id=java.id,
            time_limit_ms=1500,
            memory_limit_kb=262144,
            pids_limit=64,
            output_limit_in_bytes=None,
            repetitions=2,
        )
    )
    await session.commit()

    metadata = _metadata_input(
        start_time=(datetime.now(UTC) + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M"),
        duration_minutes=300,
        stop_answers_after=280,
        stop_updating_scoreboard=250,
    )
    result = await update_contest_metadata(
        session,
        contest,
        uberadmin,
        metadata=metadata,
        site_names=["Main"],
        language_ids=[java.id, cpp.id],
    )

    remaining_limits = (
        (await session.execute(select(ProblemLanguageLimit).where(ProblemLanguageLimit.problem_id == problem.id)))
        .scalars()
        .all()
    )

    assert result.success is True
    assert await get_contest_language_ids(session, contest) == [cpp.id, java.id]
    assert [limit.language_id for limit in remaining_limits] == [java.id]


@pytest.mark.asyncio
async def test_update_contest_metadata_rejects_language_change_after_contest_start(
    session: AsyncSession,
    running_contest,
    uberadmin,
) -> None:
    python = await _make_language(session, "python3", "Python 3")
    java = await _make_language(session, "java", "Java")
    running_contest.active = True
    running_contest.contest_timezone = "UTC"
    await session.execute(
        insert(contest_languages_table),
        [{"contest_id": running_contest.id, "language_id": python.id}],
    )
    await session.commit()

    metadata = _metadata_input(
        start_time=running_contest.local_start_time.strftime("%Y-%m-%dT%H:%M"),
        duration_minutes=running_contest.duration_minutes,
        stop_answers_after=running_contest.stop_answers_after,
        stop_updating_scoreboard=running_contest.stop_updating_scoreboard,
        clarifications_timeout_minutes=running_contest.clarifications_timeout_minutes,
        tasks_timeout_minutes=running_contest.tasks_timeout_minutes,
        review_timeout_minutes=running_contest.review_timeout_minutes,
    )
    result = await update_contest_metadata(
        session,
        running_contest,
        uberadmin,
        metadata=metadata,
        site_names=["Main"],
        language_ids=[java.id],
    )

    assert result.success is False
    assert "Allowed languages can only be changed before contest start." in result.errors
    assert await get_contest_language_ids(session, running_contest) == [python.id]


@pytest.mark.asyncio
async def test_deactivate_past_contest_marks_active_past_contest_inactive(
    session: AsyncSession,
    stopped_contest: Contest,
) -> None:
    result = await deactivate_past_contest(session, stopped_contest.id)

    assert result is not None
    assert result.active is False

    persisted = await session.get(Contest, stopped_contest.id)
    assert persisted is not None
    assert persisted.active is False


@pytest.mark.asyncio
async def test_deactivate_past_contest_rejects_non_past_or_inactive_contests(
    session: AsyncSession,
    running_contest: Contest,
    stopped_contest: Contest,
    uberadmin,
) -> None:
    upcoming = await _make_upcoming_contest(session, created_by_uberadmin_id=uberadmin.id)
    stopped_contest.active = False
    await session.commit()

    assert await deactivate_past_contest(session, running_contest.id) is None
    assert await deactivate_past_contest(session, upcoming.id) is None
    assert await deactivate_past_contest(session, stopped_contest.id) is None


@pytest.mark.asyncio
async def test_active_and_inactive_contest_queries_partition_by_active_flag(
    session: AsyncSession,
    stopped_contest: Contest,
    running_contest: Contest,
    uberadmin,
) -> None:
    upcoming = await _make_upcoming_contest(session, created_by_uberadmin_id=uberadmin.id)
    stopped_contest.active = False
    await session.commit()

    active_groups = await get_active_contests_grouped(session)
    inactive_contests = await get_inactive_contests(session)

    assert stopped_contest.id not in {contest.id for contest in active_groups.past_contests}
    assert running_contest.id in {contest.id for contest in active_groups.live_contests}
    assert upcoming.id in {contest.id for contest in active_groups.upcoming_contests}
    assert [contest.id for contest in inactive_contests] == [stopped_contest.id]
