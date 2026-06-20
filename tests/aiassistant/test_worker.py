#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the AI Assistant worker job processing path."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import openai
import pytest
import sqlalchemy as sa

from aiassistant import worker
from aiassistant.reviewer import ReviewResult
from shared.db_schema import (
    arena_ai_batch_jobs,
    arena_notifications,
    arena_problems,
    arena_submission_ai_reviews,
    arena_submissions,
    arena_users,
    languages,
)
from shared.enumerations import ArenaNotificationKind, ArenaRole
from shared.services.valkey_service.worker_commands import LivePauseFlag
from shared.services.worker_pause_state import bump_worker_pause_state


async def _seed_review_submission(
    engine: object,
    *,
    submission_id: str = "submission-ai-1",
    user_id: str = "arena-user-ai-1",
    problem_id: str = "arena-problem-ai-1",
    language_id: str = "python3",
    with_existing_review: bool = False,
    prefered_language: str = "en-US",
    problem_image_base64: str | None = None,
    problem_image_mime: str | None = None,
    problem_image_caption: str | None = None,
) -> str:
    """Insert a minimal Arena submission graph for worker tests."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        await conn.execute(
            languages.insert().values(
                id=language_id,
                name="Python 3",
                icon="python",
                compile_image="noca/python:compile",
                run_image="noca/python:run",
                compile_cmd=None,
                run_cmd=["python3", "main.py"],
                source_filename="main.py",
                artifact_path="/sandbox/main.py",
                artifact_is_source=True,
                compile_timeout_s=10.0,
                profiling_repetitions_default=3,
                profiled_pids_floor=32,
                active=True,
            )
        )
        await conn.execute(
            arena_users.insert().values(
                id=user_id,
                nome="AI Worker User",
                dta_nascimento=date(1995, 1, 1),
                email_normalizado=f"{user_id}@test.example.com",
                password_hash="hash",
                role=ArenaRole.ARENA_USER.value,
                _ai_api_key=None,
                prefered_language=prefered_language,
            )
        )
        await conn.execute(
            arena_problems.insert().values(
                id=problem_id,
                arena_number=1,
                title="Worker Problem",
                owner_id=user_id,
                problem_statement="# Statement\nSolve it.",
                enabled=True,
                problem_image_base64=problem_image_base64,
                problem_image_mime=problem_image_mime,
                problem_image_caption=problem_image_caption,
            )
        )
        await conn.execute(
            arena_submissions.insert().values(
                id=submission_id,
                user_id=user_id,
                problem_id=problem_id,
                language_id=language_id,
                source_code="print('hello')",
                source_hash="a" * 64,
                source_size_bytes=14,
                submit_to_ai=True,
            )
        )
        if with_existing_review:
            await conn.execute(
                arena_submission_ai_reviews.insert().values(
                    submission_id=submission_id,
                    ai_response="Already reviewed.",
                    ai_response_at=datetime.now(UTC),
                    _ai_review_cost=None,
                    used_platform_key=False,
                )
            )
    return submission_id


@pytest.mark.asyncio
async def test_dequeue_loop_suppresses_intake_until_resumed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A paused AI worker waits and resumes queue intake after the flag clears."""
    stop_event = worker.asyncio.Event()
    pause_flag = LivePauseFlag(paused=True)
    dequeue = AsyncMock()

    async def _fake_wait_for(awaitable: object, timeout: float) -> None:
        del timeout
        awaitable.close()  # type: ignore[attr-defined]
        pause_flag.paused = False
        raise TimeoutError

    async def _fake_dequeue(_runtime: object) -> None:
        stop_event.set()
        return None

    dequeue.side_effect = _fake_dequeue
    monkeypatch.setattr(worker.asyncio, "wait_for", _fake_wait_for)
    monkeypatch.setattr(worker, "dequeue_arena_ai_review_job_id", dequeue)

    await worker._dequeue_loop(
        engine=object(),
        valkey_runtime=object(),  # type: ignore[arg-type]
        stop_event=stop_event,
        pause_flag=pause_flag,
        worker_id="ai-test-worker",
    )

    dequeue.assert_awaited_once()


@pytest.mark.asyncio
async def test_aiassistant_startup_restores_paused_state(engine: object) -> None:
    """AI assistant startup restores committed pause state from PostgreSQL."""
    worker_id = "ai-startup-paused"
    async with engine.begin() as conn:  # type: ignore[attr-defined]
        await bump_worker_pause_state(
            conn,
            worker_class="aiassistant",
            worker_id=worker_id,
            paused=True,
            paused_by="admin@example.com",
        )

    pause_flag = LivePauseFlag()
    await worker._restore_startup_pause_state(engine, worker_id, pause_flag)

    assert pause_flag.paused is True
    assert pause_flag.paused_by == "admin@example.com"
    assert pause_flag.applied_generation == 1


async def _fetch_ai_review(engine: object, submission_id: str) -> sa.RowMapping | None:
    """Return the arena_submission_ai_reviews row for a submission, or None."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    sa.select(arena_submission_ai_reviews).where(
                        arena_submission_ai_reviews.c.submission_id == submission_id
                    )
                )
            )
            .mappings()
            .first()
        )
    return row


async def _fetch_batch_job(engine: object, submission_id: str) -> sa.RowMapping | None:
    """Return the arena_ai_batch_jobs row for a submission, or None."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    sa.select(arena_ai_batch_jobs).where(arena_ai_batch_jobs.c.submission_id == submission_id)
                )
            )
            .mappings()
            .first()
        )
    return row


async def _fetch_ai_review_notification(engine: object, submission_id: str) -> sa.RowMapping | None:
    """Return the Arena notification row for a completed AI review, or None."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    sa.select(arena_notifications).where(
                        arena_notifications.c.source_ref == submission_id,
                        arena_notifications.c.notification_kind == ArenaNotificationKind.AI_REVIEW_COMPLETED.value,
                    )
                )
            )
            .mappings()
            .first()
        )
    return row


@pytest.mark.asyncio
async def test_process_job_platform_key_dispatches_to_batch(engine: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """Platform key path stages the job without an OpenAI call and cleans Valkey state."""
    submission_id = await _seed_review_submission(engine)
    monkeypatch.setattr(worker.settings, "OPENAI_API_KEY", "sk-platform")

    complete_job = AsyncMock()

    with patch.object(worker, "complete_arena_ai_review_job", complete_job):
        await worker._process_job(submission_id, engine, object())  # type: ignore[arg-type]

    # Staged row inserted with null openai_batch_id — actual batch submission is deferred
    batch_row = await _fetch_batch_job(engine, submission_id)
    assert batch_row is not None
    assert batch_row["local_status"] == "staged"
    assert batch_row["openai_batch_id"] is None

    # No immediate AI review row — deferred to poller after flusher runs
    assert await _fetch_ai_review(engine, submission_id) is None

    # Valkey job state removed after DB write
    complete_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_job_prefers_user_key_and_records_cost(engine: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """A user API key wins over the platform key; cost is still recorded and used_platform_key is False."""
    submission_id = await _seed_review_submission(engine, submission_id="submission-ai-user-key")
    monkeypatch.setattr(worker.settings, "OPENAI_API_KEY", "sk-platform")

    review = ReviewResult(
        response_text="Trace the sample manually.",
        input_tokens=100,
        output_tokens=50,
        total_cost=0.000625,
        used_platform_key=False,
    )
    call_ai_review = AsyncMock(return_value=review)

    with (
        patch.object(worker, "get_user_api_key", AsyncMock(return_value="sk-user")),
        patch.object(worker, "call_ai_review", call_ai_review),
        patch.object(worker, "complete_arena_ai_review_job", AsyncMock()),
    ):
        await worker._process_job(submission_id, engine, object())  # type: ignore[arg-type]

    row = await _fetch_ai_review(engine, submission_id)
    assert row is not None
    assert row["ai_response"] == "Trace the sample manually."
    assert row["_ai_review_cost"] == 625
    assert row["used_platform_key"] is False
    assert call_ai_review.await_args.kwargs["api_key"] == "sk-user"
    assert call_ai_review.await_args.kwargs["is_platform_key"] is False


@pytest.mark.asyncio
async def test_process_job_user_key_passes_prefered_language_instruction(
    engine: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Online review task content includes the user's preferred locale instruction."""
    submission_id = await _seed_review_submission(
        engine,
        submission_id="submission-ai-user-lang",
        user_id="arena-user-ai-lang",
        problem_id="arena-problem-ai-lang",
        prefered_language="pt-BR",
    )
    monkeypatch.setattr(worker.settings, "OPENAI_API_KEY", "sk-platform")

    review = ReviewResult(
        response_text="Revise os limites do loop.",
        input_tokens=100,
        output_tokens=50,
        total_cost=0.000625,
        used_platform_key=False,
    )
    call_ai_review = AsyncMock(return_value=review)

    with (
        patch.object(worker, "get_user_api_key", AsyncMock(return_value="sk-user")),
        patch.object(worker, "call_ai_review", call_ai_review),
        patch.object(worker, "complete_arena_ai_review_job", AsyncMock()),
    ):
        await worker._process_job(submission_id, engine, object())  # type: ignore[arg-type]

    call_ai_review.assert_awaited_once()
    assert call_ai_review.await_args.kwargs["extra_task_instructions"] == (
        "Respond in the user's preferred language: Brazilian Portuguese (pt-BR)."
    )


@pytest.mark.asyncio
async def test_process_job_skips_existing_ai_review(engine: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """Submissions with an existing review row are cleaned up without another API call."""
    submission_id = await _seed_review_submission(
        engine,
        submission_id="submission-ai-existing",
        with_existing_review=True,
    )
    monkeypatch.setattr(worker.settings, "OPENAI_API_KEY", "sk-platform")
    call_ai_review = AsyncMock()
    complete_job = AsyncMock()

    with (
        patch.object(worker, "call_ai_review", call_ai_review),
        patch.object(worker, "complete_arena_ai_review_job", complete_job),
    ):
        await worker._process_job(submission_id, engine, object())  # type: ignore[arg-type]

    call_ai_review.assert_not_awaited()
    complete_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_job_without_any_api_key_cleans_inflight_without_review(
    engine: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A submission without user or platform key is skipped and no review row is created."""
    submission_id = await _seed_review_submission(engine, submission_id="submission-ai-no-key")
    monkeypatch.setattr(worker.settings, "OPENAI_API_KEY", None)
    call_ai_review = AsyncMock()
    complete_job = AsyncMock()

    with (
        patch.object(worker, "call_ai_review", call_ai_review),
        patch.object(worker, "complete_arena_ai_review_job", complete_job),
    ):
        await worker._process_job(submission_id, engine, object())  # type: ignore[arg-type]

    row = await _fetch_ai_review(engine, submission_id)
    assert row is None
    call_ai_review.assert_not_awaited()
    complete_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_job_missing_submission_cleans_inflight(engine: object) -> None:
    """Missing DB rows are treated as stale queue entries and cleaned up."""
    complete_job = AsyncMock()

    with patch.object(worker, "complete_arena_ai_review_job", complete_job):
        await worker._process_job("missing-submission", engine, object())  # type: ignore[arg-type]

    complete_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_job_platform_key_stages_regardless_of_problem_content(
    engine: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Platform key path stages the submission regardless of image or language settings."""
    submission_id = await _seed_review_submission(
        engine,
        submission_id="submission-ai-image",
        user_id="arena-user-ai-img",
        problem_id="arena-problem-ai-img",
        problem_image_base64="aGVsbG8=",
        problem_image_mime="image/png",
        problem_image_caption="Directed graph with 4 nodes.",
    )
    monkeypatch.setattr(worker.settings, "OPENAI_API_KEY", "sk-platform")

    with patch.object(worker, "complete_arena_ai_review_job", AsyncMock()):
        await worker._process_job(submission_id, engine, object())  # type: ignore[arg-type]

    batch_row = await _fetch_batch_job(engine, submission_id)
    assert batch_row is not None
    assert batch_row["local_status"] == "staged"


async def _fetch_submit_to_ai_flag(engine: object, submission_id: str) -> bool | None:
    """Return the submit_to_ai column value for a given submission, or None if not found."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    sa.select(arena_submissions.c.submit_to_ai).where(arena_submissions.c.id == submission_id)
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        return None
    return bool(row["submit_to_ai"])


async def _fetch_ai_review_failed_notification(engine: object, submission_id: str) -> sa.RowMapping | None:
    """Return the Arena notification row for a failed AI review, or None."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    sa.select(arena_notifications).where(
                        arena_notifications.c.source_ref == submission_id,
                        arena_notifications.c.notification_kind == ArenaNotificationKind.AI_REVIEW_FAILED.value,
                    )
                )
            )
            .mappings()
            .first()
        )
    return row


def _make_auth_error(message: str = "Invalid API key") -> openai.AuthenticationError:
    """Construct an openai.AuthenticationError suitable for use in tests."""
    response = MagicMock(spec=httpx.Response)
    response.headers = {"x-request-id": "test-req-id"}
    response.status_code = 401
    return openai.AuthenticationError(message, response=response, body=None)


@pytest.mark.asyncio
async def test_process_job_invalid_user_api_key_resets_flag_and_notifies(
    engine: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AuthenticationError from user-owned key resets flag and sends 'invalid key' notification."""
    submission_id = await _seed_review_submission(
        engine,
        submission_id="submission-ai-invalid-user-key",
        user_id="arena-user-ai-inv-u",
        problem_id="arena-problem-ai-inv-u",
    )
    monkeypatch.setattr(worker.settings, "OPENAI_API_KEY", "sk-platform")
    monkeypatch.setattr(worker.settings, "OPENAI_MODEL", "gpt-test")
    monkeypatch.setattr(worker.settings, "OPENAI_MAX_OUTPUT_TOKENS", 500)
    monkeypatch.setattr(worker.settings, "OPENAI_INPUT_TOKEN_PRICE", 1.0)
    monkeypatch.setattr(worker.settings, "OPENAI_OUTPUT_TOKEN_PRICE", 2.0)

    call_ai_review = AsyncMock(side_effect=_make_auth_error())
    complete_job = AsyncMock()

    with (
        patch.object(worker, "get_user_api_key", AsyncMock(return_value="sk-user-invalid")),
        patch.object(worker, "call_ai_review", call_ai_review),
        patch.object(worker, "complete_arena_ai_review_job", complete_job),
    ):
        await worker._process_job(submission_id, engine, object())  # type: ignore[arg-type]

    # Flag must be cleared so UI no longer shows "pending" and user can retry
    assert await _fetch_submit_to_ai_flag(engine, submission_id) is False

    # Notification must carry the user-specific "invalid key" message
    notification = await _fetch_ai_review_failed_notification(engine, submission_id)
    assert notification is not None
    assert notification["notification_kind"] == ArenaNotificationKind.AI_REVIEW_FAILED.value
    assert "invalid" in notification["message"].lower()

    # No AI review row must be created
    assert await _fetch_ai_review(engine, submission_id) is None

    # Inflight entry must be removed exactly once
    complete_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_job_platform_key_missing_api_key_is_handled(
    engine: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When OPENAI_API_KEY is not configured the job is cleaned up without staging."""
    submission_id = await _seed_review_submission(
        engine,
        submission_id="submission-ai-no-key",
        user_id="arena-user-ai-nokey",
        problem_id="arena-problem-ai-nokey",
    )
    monkeypatch.setattr(worker.settings, "OPENAI_API_KEY", None)

    complete_job = AsyncMock()

    with patch.object(worker, "complete_arena_ai_review_job", complete_job):
        await worker._process_job(submission_id, engine, object())  # type: ignore[arg-type]

    # No staged row — cannot stage without a platform key
    assert await _fetch_batch_job(engine, submission_id) is None

    # Inflight entry must be removed
    complete_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_job_batch_idempotency_guard_skips_duplicate(
    engine: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When an active batch row already exists the job is cleaned up without staging again."""
    import uuid

    from sqlalchemy.ext.asyncio import AsyncEngine

    submission_id = await _seed_review_submission(
        engine,
        submission_id="submission-ai-dup-batch",
        user_id="arena-user-ai-dup",
        problem_id="arena-problem-ai-dup",
    )
    monkeypatch.setattr(worker.settings, "OPENAI_API_KEY", "sk-platform")

    # Pre-insert an active batch row to simulate the crash-recovery scenario
    db_engine: AsyncEngine = engine  # type: ignore[assignment]

    async with db_engine.begin() as conn:
        await conn.execute(
            arena_ai_batch_jobs.insert().values(
                id=str(uuid.uuid4()),
                submission_id=submission_id,
                openai_batch_id="batch-preexisting",
                local_status="submitted",
            )
        )

    complete_job = AsyncMock()

    with patch.object(worker, "complete_arena_ai_review_job", complete_job):
        await worker._process_job(submission_id, engine, object())  # type: ignore[arg-type]

    # Still exactly one batch row (not duplicated)
    batch_row = await _fetch_batch_job(engine, submission_id)
    assert batch_row is not None
    assert batch_row["openai_batch_id"] == "batch-preexisting"
    # Inflight cleaned up
    complete_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_job_batch_guard_replaces_terminal_row(engine: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """A spent terminal batch row is deleted so a re-request can stage a fresh job."""
    submission_id = await _seed_review_submission(
        engine,
        submission_id="submission-ai-term-batch",
        user_id="arena-user-ai-term",
        problem_id="arena-problem-ai-term",
    )
    monkeypatch.setattr(worker.settings, "OPENAI_API_KEY", "sk-platform")
    monkeypatch.setattr(worker.settings, "OPENAI_MODEL", "gpt-test")
    monkeypatch.setattr(worker.settings, "OPENAI_MAX_OUTPUT_TOKENS", 500)

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    import uuid

    # A previous attempt that failed at OpenAI: terminal row, no review stored.
    async with db_engine.begin() as conn:
        await conn.execute(
            arena_ai_batch_jobs.insert().values(
                id=str(uuid.uuid4()),
                submission_id=submission_id,
                openai_batch_id="batch-failed-old",
                local_status="failed",
            )
        )

    complete_job = AsyncMock()

    with patch.object(worker, "complete_arena_ai_review_job", complete_job):
        await worker._process_job(submission_id, engine, object())  # type: ignore[arg-type]

    # The terminal row no longer blocks: a fresh staged row is inserted.
    batch_row = await _fetch_batch_job(engine, submission_id)
    assert batch_row is not None
    assert batch_row["local_status"] == "staged"
    assert batch_row["openai_batch_id"] is None


@pytest.mark.asyncio
async def test_dequeue_loop_publishes_last_job_after_dequeue(monkeypatch: pytest.MonkeyPatch) -> None:
    """publish_worker_last_job is awaited when a submission_id is successfully dequeued."""
    stop_event = worker.asyncio.Event()
    pause_flag = LivePauseFlag()
    published: list[str] = []

    async def _fake_dequeue(_runtime: object) -> str:
        return "sub-abc"

    async def _fake_process(submission_id: str, *args: object, **kwargs: object) -> None:
        stop_event.set()

    async def _fake_publish_last_job(client: object, *, worker_class: object, worker_id: str) -> None:
        published.append(worker_id)

    monkeypatch.setattr(worker, "dequeue_arena_ai_review_job_id", _fake_dequeue)
    monkeypatch.setattr(worker, "get_ai_review_job_hash", AsyncMock(return_value=None))
    monkeypatch.setattr(worker, "_process_job", _fake_process)
    monkeypatch.setattr(worker, "publish_worker_last_job", _fake_publish_last_job)

    await worker._dequeue_loop(
        engine=object(),
        valkey_runtime=object(),  # type: ignore[arg-type]
        stop_event=stop_event,
        pause_flag=pause_flag,
        worker_id="ai-worker-1",
    )

    assert published == ["ai-worker-1"]


@pytest.mark.asyncio
async def test_dequeue_loop_does_not_publish_last_job_on_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    """publish_worker_last_job is NOT called when the queue is idle (submission_id is None)."""
    stop_event = worker.asyncio.Event()
    pause_flag = LivePauseFlag()
    published: list[str] = []
    call_count = 0

    async def _fake_dequeue(_runtime: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            stop_event.set()
        return None

    async def _fake_wait_for(awaitable: object, timeout: float) -> None:
        del timeout
        import inspect

        if inspect.iscoroutine(awaitable):
            awaitable.close()
        raise TimeoutError

    async def _fake_publish_last_job(client: object, *, worker_class: object, worker_id: str) -> None:
        published.append(worker_id)

    monkeypatch.setattr(worker, "dequeue_arena_ai_review_job_id", _fake_dequeue)
    monkeypatch.setattr(worker.asyncio, "wait_for", _fake_wait_for)
    monkeypatch.setattr(worker, "publish_worker_last_job", _fake_publish_last_job)

    await worker._dequeue_loop(
        engine=object(),
        valkey_runtime=object(),  # type: ignore[arg-type]
        stop_event=stop_event,
        pause_flag=pause_flag,
        worker_id="ai-worker-1",
    )

    assert published == []
