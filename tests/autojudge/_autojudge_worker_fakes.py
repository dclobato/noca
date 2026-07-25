#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared fakes and seed builders for the autojudge worker test modules.

``_FakeValkey`` stands in for the queue so the worker pipeline tests run without
a Valkey server; the builders seed the language/submission/judgment rows the
pipeline needs. Imported by the ``test_autojudge_worker*`` modules and by the
dispatch tests.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from autojudge.db import QueuedSubmission
from shared.enumerations import JudgmentStatus
from web.models.language import Language
from web.models.problem import Problem
from web.models.submission import Submission, SubmissionJudgment
from web.models.users import User


def _uid() -> str:
    return str(uuid.uuid4())


def _jobs_completed_value(job_kind: str, outcome: str) -> float:
    """Read the current jobs_completed counter value for one label pair."""
    from autojudge.metrics import JOBS_COMPLETED_TOTAL

    return JOBS_COMPLETED_TOTAL.labels(job_kind=job_kind, outcome=outcome)._value.get()


def _make_language(session: AsyncSession) -> Language:
    lang = Language(
        id="python3",
        name="Python 3.14",
        icon="python",
        compile_image="noca/judge-python3:compile",
        run_image="noca/judge-python3:run",
        compile_cmd=["python3", "-m", "py_compile", "/sandbox/source.py"],
        run_cmd=["python3", "-u", "/sandbox/source.py"],
        source_filename="source.py",
        artifact_path="/sandbox/source.py",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(lang)
    return lang


def _make_submission(
    session: AsyncSession,
    problem: Problem,
    team: User,
    language: Language,
) -> Submission:
    src = "print('hello')"
    sub = Submission(
        problem_id=problem.id,
        team_id=team.id,
        language_id=language.id,
        source_code=src,
        source_hash=hashlib.sha256(src.encode()).hexdigest(),
        source_size_bytes=len(src.encode()),
    )
    session.add(sub)
    return sub


def _make_judgment(
    session: AsyncSession,
    submission: Submission,
    status: JudgmentStatus = JudgmentStatus.QUEUED,
) -> SubmissionJudgment:
    j = SubmissionJudgment(submission_id=submission.id, status=status)
    session.add(j)
    return j


def _make_qs(seed: dict) -> QueuedSubmission:
    """Build the queued payload the pipeline expects from a ``seed_data`` dict."""
    j = seed["judgment"]
    sub = seed["submission"]
    return QueuedSubmission(
        judgment_id=j.id,
        submission_id=sub.id,
        contest_id=seed["contest"].id,
        contest_start_time=seed["contest"].start_time,
        problem_id=seed["problem"].id,
        team_id=sub.team_id,
        language_id=sub.language_id,
        source_code=sub.source_code,
        autojudge_only=True,
        accept_pe=False,
        stop_updating_scoreboard=120,
    )


class _FakePipeline:
    """Fake pipeline that records commands for _FakeValkey."""

    def __init__(self, parent: _FakeValkey) -> None:
        self.parent = parent
        self.commands: list[tuple[str, Any, Any]] = []

    def lrem(self, name: str, count: int, value: str) -> None:
        self.commands.append(("lrem", name, (count, value)))

    def zrem(self, name: str, *values: str) -> None:
        self.commands.append(("zrem", name, values))

    def delete(self, *keys: str) -> None:
        self.commands.append(("delete", keys, None))

    async def execute(self) -> None:
        """Apply the queued commands through the parent, so they are recorded."""
        for op, key, payload in self.commands:
            if op == "lrem":
                count, value = payload
                await self.parent.lrem(key, count, value)
            elif op == "zrem":
                await self.parent.zrem(key, *payload)
            elif op == "delete":
                await self.parent.delete(*key)


class _FakeValkey:
    """Minimal Valkey replacement that records calls and supports lock checks.

    Call recording (``set_calls``, ``delete_calls``, ``lrem_calls``) exists for
    the lock tests, which assert on the arguments of the ``SET NX`` that claims
    ``judge:lock:<job_id>`` rather than on its side effects.
    """

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.published: list[tuple[str, Any]] = []
        self.set_calls: list[dict[str, Any]] = []
        self.delete_calls: list[tuple[str, ...]] = []
        self.lrem_calls: list[tuple[str, int, str]] = []

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> str:
        """Execute the claimed-job cleanup script used by dispatch tests."""
        if numkeys != 4 or "not_owner" not in script:
            raise AssertionError("Fake Valkey received an unsupported Lua script")
        inflight_key, times_key, job_key, lock_key, job_id, lock_token = keys_and_args
        if self.data.get(lock_key) != lock_token:
            return "not_owner"
        await self.delete(job_key)
        await self.lrem(inflight_key, 0, job_id)
        await self.zrem(times_key, job_id)
        await self.delete(lock_key)
        return "cleaned"

    async def set(self, key: str, value: Any, *, nx: bool = False, ex: int | None = None) -> bool | None:
        self.set_calls.append({"key": key, "value": value, "nx": nx, "ex": ex})
        if nx and key in self.data:
            return None  # Valkey returns nil (falsy) when NX finds the key set
        self.data[key] = value
        return True

    async def zadd(self, name: str, mapping: dict[str, float]) -> int:
        zset = self.data.setdefault(name, {})
        if not isinstance(zset, dict):
            zset = {}
            self.data[name] = zset
        for key, value in mapping.items():
            zset[key] = value
        return len(mapping)

    async def delete(self, *keys: str) -> int:
        self.delete_calls.append(keys)
        count = 0
        for k in keys:
            if k in self.data:
                del self.data[k]
                count += 1
        return count

    async def lrem(self, name: str, count: int, value: str) -> int:
        self.lrem_calls.append((name, count, value))
        items = self.data.get(name, [])
        if not isinstance(items, list):
            return 0
        removed = 0
        kept = []
        for item in items:
            if item == value and (count == 0 or removed < count):
                removed += 1
                continue
            kept.append(item)
        self.data[name] = kept
        return removed

    async def zrem(self, name: str, *values: str) -> int:
        zset = self.data.get(name, {})
        if not isinstance(zset, dict):
            return 0
        removed = 0
        for value in values:
            if value in zset:
                del zset[value]
                removed += 1
        return removed

    async def lrange(self, name: str, start: int, end: int) -> list[Any]:
        items = self.data.get(name, [])
        if not isinstance(items, list):
            return []
        if end == -1:
            end = len(items) - 1
        return items[start : end + 1]

    async def hgetall(self, name: str) -> dict[str, Any]:
        value = self.data.get(name, {})
        return value if isinstance(value, dict) else {}

    async def hget(self, name: str, key: str) -> Any:
        value = self.data.get(name, {})
        if not isinstance(value, dict):
            return None
        return value.get(key)

    async def hset(
        self,
        name: str,
        key: str | None = None,
        value: Any | None = None,
        *,
        mapping: dict[str, Any] | None = None,
    ) -> int:
        bucket = self.data.setdefault(name, {})
        if not isinstance(bucket, dict):
            bucket = {}
            self.data[name] = bucket
        if mapping is not None:
            bucket.update(mapping)
            return len(mapping)
        if key is None:
            return 0
        bucket[key] = value
        return 1

    async def lpush(self, name: str, *values: Any) -> int:
        items = self.data.setdefault(name, [])
        if not isinstance(items, list):
            items = []
            self.data[name] = items
        for value in values:
            items.insert(0, value)
        return len(items)

    async def rpush(self, name: str, *values: Any) -> int:
        items = self.data.setdefault(name, [])
        if not isinstance(items, list):
            items = []
            self.data[name] = items
        items.extend(values)
        return len(items)

    async def publish(self, channel: str, message: Any) -> int:
        self.published.append((channel, message))
        return 1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
