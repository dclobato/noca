#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Smoke test the Arena submission service and autojudge worker adapter.

Usage:
    uv run python scripts/arena/smoke_test_arena_judge_flow.py --language python3 --accepted
    uv run python scripts/arena/smoke_test_arena_judge_flow.py --language python3 --wrong
    uv run python scripts/arena/smoke_test_arena_judge_flow.py --language python3 --accepted --external-worker
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import docker
from sqlalchemy import delete, select

sys.path.insert(0, str(Path(__file__).parents[2]))

import arena.models  # noqa: E402, F401
import web.models  # noqa: E402, F401
from arena.database import create_engine, create_session_factory  # noqa: E402
from arena.models.arena_problems import ArenaProblem, ArenaRatingProblem, ArenaTestCase  # noqa: E402
from arena.models.arena_submissions import ArenaSubmissionJudgment, ArenaSubmissionTestResult  # noqa: E402
from arena.models.arena_users import ArenaUser  # noqa: E402
from arena.services.submission_service import create_arena_submission  # noqa: E402
from arena.services.valkey_service import create_arena_valkey_runtime, enqueue_arena_submission_job  # noqa: E402
from autojudge.config import settings as judge_settings  # noqa: E402
from autojudge.db import open_db  # noqa: E402
from autojudge.pool import PoolManager  # noqa: E402
from autojudge.queue_ops import get_job_kind  # noqa: E402
from autojudge.worker import _dispatch_job  # noqa: E402
from shared.enumerations import ArenaRole, JudgmentStatus  # noqa: E402
from shared.language_registry import registry_from_rows  # noqa: E402
from web.models.language import Language  # noqa: E402


def _source(language_id: str, accepted: bool) -> str:
    if language_id != "python3":
        raise RuntimeError("This smoke script currently ships built-in source only for python3.")
    return "print(input())\n" if accepted else "print('wrong')\n"


async def _ensure_seed_data(session, language_id: str) -> tuple[ArenaUser, ArenaProblem]:
    language = await session.get(Language, language_id)
    if language is None or not language.active:
        raise RuntimeError(f"Active language '{language_id}' not found. Seed languages before running this smoke test.")

    user = (
        await session.execute(select(ArenaUser).where(ArenaUser.email_normalizado == "arena-smoke@noca.local"))
    ).scalar_one_or_none()
    if user is None:
        user = ArenaUser(
            nome="Arena Smoke User",
            email_normalizado="arena-smoke@noca.local",
            dta_nascimento=date(1990, 1, 1),
            role=ArenaRole.ARENA_USER,
            ativo=True,
            email_confirmado=True,
        )
        user.password = "SmokePass1!"
        session.add(user)
        await session.flush()

    problem = (
        await session.execute(select(ArenaProblem).where(ArenaProblem.title == "Arena Smoke Echo"))
    ).scalar_one_or_none()
    if problem is None:
        problem = ArenaProblem(
            title="Arena Smoke Echo",
            owner_id=user.id,
            problem_statement="<p>Echo one line.</p>",
            time_limit_ms=2000,
            memory_limit_kb=262144,
            pids_limit=64,
            output_limit_in_bytes=65536,
        )
        session.add(problem)
        await session.flush()
        session.add(ArenaRatingProblem(problem_id=problem.id))
        session.add(ArenaTestCase(problem_id=problem.id, ordinal=1, input_content="hello\n", output_content="hello\n"))
        session.add(ArenaTestCase(problem_id=problem.id, ordinal=2, input_content="world\n", output_content="world\n"))
        await session.flush()

    return user, problem


async def _cleanup(session, submission_id: str) -> None:
    from shared.db_schema.arena import arena_submissions

    await session.execute(delete(arena_submissions).where(arena_submissions.c.id == submission_id))


async def _print_and_check_result(
    session_factory, submission_id: str, judgment_id: str, cleanup: bool, accepted: bool
) -> int:
    async with session_factory() as session:
        judgment = await session.get(ArenaSubmissionJudgment, judgment_id)
        if judgment is None:
            raise RuntimeError("Judgment disappeared after dispatch.")
        test_result = (
            await session.execute(
                select(ArenaSubmissionTestResult).where(ArenaSubmissionTestResult.judgment_id == judgment_id)
            )
        ).scalar_one_or_none()
        print(f"submission_id={submission_id}")
        print(f"judgment_id={judgment_id}")
        print(f"status={judgment.status}")
        print(f"final_verdict={judgment.final_verdict}")
        print(f"first_non_ac={test_result.verdict if test_result else None}")
        if cleanup:
            await _cleanup(session, submission_id)
            await session.commit()

        expected_verdict = "AC" if accepted else "WA"
        return 0 if judgment.final_verdict == expected_verdict else 1


async def _wait_for_external_worker(
    session_factory, judgment_id: str, timeout_s: float, poll_interval_s: float
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    terminal = {
        JudgmentStatus.DONE.value,
        JudgmentStatus.FAILED.value,
        JudgmentStatus.SUPERSEDED.value,
    }
    last_status: str | None = None
    while True:
        async with session_factory() as session:
            judgment = await session.get(ArenaSubmissionJudgment, judgment_id)
            if judgment is None:
                raise RuntimeError("Judgment disappeared while waiting for external worker.")
            last_status = judgment.status
            if judgment.status in terminal:
                return

        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(f"Timed out waiting for external worker; last status was {last_status!r}")
        await asyncio.sleep(poll_interval_s)


async def _dispatch_locally(engine, valkey_runtime, language_id: str, judgment_id: str) -> None:
    executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="arena-smoke-docker")
    docker_client = docker.DockerClient(base_url=judge_settings.DOCKER_BASE_URL, timeout=60)
    pool_manager: PoolManager | None = None
    try:
        async with open_db(engine) as db:
            language_rows = await db.list_languages()
            language_registry = registry_from_rows(language_rows)
            pool_manager = PoolManager(language_registry, language_ids=[language_id])
            await pool_manager.warm()

            job_id = await valkey_runtime.dequeue_job_id()
            if job_id != judgment_id:
                raise RuntimeError(f"Expected queued job {judgment_id}, got {job_id}")
            job_kind = await get_job_kind(valkey_runtime._client, job_id)  # noqa: SLF001
            await _dispatch_job(
                job_id=job_id,
                job_kind=job_kind,
                db=db,
                valkey=valkey_runtime._client,  # noqa: SLF001
                pool_manager=pool_manager,
                language_registry=language_registry,
                docker_client=docker_client,
                executor=executor,
                wid="arena-smoke",
            )
    finally:
        if pool_manager is not None:
            await pool_manager.shutdown()
        docker_client.close()
        executor.shutdown(wait=False)


async def main(
    language_id: str,
    accepted: bool,
    cleanup: bool,
    external_worker: bool,
    timeout_s: float,
    poll_interval_s: float,
) -> int:
    engine = create_engine(judge_settings.db_url)
    session_factory = create_session_factory(engine)
    valkey_runtime = create_arena_valkey_runtime(healthcheck_interval_s=5)
    await valkey_runtime.start()
    submission_id: str | None = None

    try:
        async with session_factory() as session:
            user, problem = await _ensure_seed_data(session, language_id)
            result = await create_arena_submission(
                session=session,
                user_id=user.id,
                problem_id=problem.id,
                language_id=language_id,
                source_code=_source(language_id, accepted),
            )
            submission_id = result.submission.id
            judgment_id = result.judgment.id
            await session.commit()

        await enqueue_arena_submission_job(valkey_runtime, result.job)

        if external_worker:
            print(f"queued_submission_id={submission_id}")
            print(f"queued_judgment_id={judgment_id}")
            await _wait_for_external_worker(session_factory, judgment_id, timeout_s, poll_interval_s)
        else:
            await _dispatch_locally(engine, valkey_runtime, language_id, judgment_id)

        return await _print_and_check_result(session_factory, submission_id, judgment_id, cleanup, accepted)
    finally:
        await valkey_runtime.stop()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Smoke test Arena autojudge flow")
    parser.add_argument("--language", default="python3")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--accepted", action="store_true", help="Submit an accepted solution")
    mode.add_argument("--wrong", action="store_true", help="Submit a wrong-answer solution")
    parser.add_argument("--cleanup", action="store_true", help="Delete the smoke submission after judging")
    parser.add_argument(
        "--external-worker",
        action="store_true",
        help="Only enqueue the job and poll the database; requires a running noca-autojudge process.",
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="Seconds to wait for --external-worker")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="Polling interval for --external-worker")
    args = parser.parse_args()
    sys.exit(
        asyncio.run(
            main(
                args.language,
                not args.wrong,
                args.cleanup,
                args.external_worker,
                args.timeout,
                args.poll_interval,
            )
        )
    )
