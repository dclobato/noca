from __future__ import annotations

import argparse
from uuid import uuid4

from shared.enumerations import JudgmentStatus
from web.config import settings as web_settings
from web.models.language import Language
from web.models.submission import Submission, SubmissionJudgment


def _fake_script_settings(*, db_url: str):
    class _Settings:
        queue_job_hash_prefix = "judge:job"
        valkey_url = web_settings.valkey_url

        @property
        def db_url(self) -> str:
            return db_url

    return _Settings()


async def _seed_submission_and_judgment(session, contest_problem, team_user) -> SubmissionJudgment:
    language = Language(
        id=f"py-{uuid4()}",
        name="Python 3",
        icon="python",
        compile_image="python:3.12",
        run_image="python:3.12",
        compile_cmd=None,
        run_cmd=["python3", "main.py"],
        source_filename="main.py",
        artifact_path="main.py",
        artifact_is_source=True,
        compile_timeout_s=30.0,
        active=True,
    )
    session.add(language)
    await session.flush()

    submission = Submission(
        problem_id=contest_problem.id,
        team_id=team_user.id,
        language_id=language.id,
        source_code="print('ok')",
        source_hash="a" * 64,
        source_size_bytes=10,
        timestamp_seconds=0,
    )
    session.add(submission)
    await session.flush()

    judgment = SubmissionJudgment(
        submission_id=submission.id,
        status=JudgmentStatus.QUEUED,
    )
    session.add(judgment)
    await session.commit()
    return judgment


async def test_backfill_updates_missing_contest_id(
    monkeypatch,
    engine,
    session,
    contest_problem,
    team_user,
    valkey_client,
) -> None:
    from scripts.web import backfill_job_contest_id as script

    judgment = await _seed_submission_and_judgment(session, contest_problem, team_user)
    job_key = f"{web_settings.queue_job_hash_prefix}:{judgment.id}"

    await valkey_client.hset(
        job_key,
        mapping={
            "judgment_id": judgment.id,
            "is_rejudge": "false",
            "requeue_count": "0",
        },
    )

    monkeypatch.setattr(script, "settings", _fake_script_settings(db_url=str(engine.url)))

    args = argparse.Namespace(batch_size=100, write_sleep_ms=0, max_updates=None, dry_run=False)
    exit_code = await script.run_backfill(args)

    assert exit_code == 0
    job_hash = await valkey_client.hgetall(job_key)
    assert job_hash["contest_id"] == contest_problem.contest_id
    assert job_hash["submission_id"] == judgment.submission_id


async def test_backfill_dry_run_does_not_write(
    monkeypatch,
    engine,
    session,
    contest_problem,
    team_user,
    valkey_client,
) -> None:
    from scripts.web import backfill_job_contest_id as script

    judgment = await _seed_submission_and_judgment(session, contest_problem, team_user)
    job_key = f"{web_settings.queue_job_hash_prefix}:{judgment.id}"

    await valkey_client.hset(
        job_key,
        mapping={
            "judgment_id": judgment.id,
            "is_rejudge": "false",
            "requeue_count": "0",
        },
    )

    monkeypatch.setattr(script, "settings", _fake_script_settings(db_url=str(engine.url)))

    args = argparse.Namespace(batch_size=100, write_sleep_ms=0, max_updates=None, dry_run=True)
    exit_code = await script.run_backfill(args)

    assert exit_code == 0
    job_hash = await valkey_client.hgetall(job_key)
    assert "contest_id" not in job_hash
