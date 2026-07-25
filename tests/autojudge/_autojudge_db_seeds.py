#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared row builders for the autojudge database test modules.

Seed data is inserted through the web/arena ORM so the worker's Core queries are
verified against rows the applications would really produce.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_problems import (
    ArenaTestCase,
)
from arena.models.arena_users import ArenaUser
from autojudge.config import settings as autojudge_settings
from shared.enumerations import (
    ArenaRole,
    JudgmentStatus,
)
from shared.services.testcase_files import save_testcase_files
from web.models.language import Language
from web.models.problem import Problem
from web.models.submission import (
    Submission,
    SubmissionJudgment,
)
from web.models.users import User

#: Claim seeded judgments carry, so accessor tests can act as the owning attempt
#: without going through a dispatch.
TEST_ATTEMPT_TOKEN = "worker-test:attempt-1"


def _uid() -> str:
    return str(uuid.uuid4())


def _add_arena_tc(
    session: AsyncSession,
    problem_id: str,
    ordinal: int,
    input_content: str = "1\n",
    output_content: str = "1\n",
) -> ArenaTestCase:
    """Write Arena test-case files to the autojudge arena root and add a row.

    Content lives on disk (``<root>/arena/<problem_id>/NNN.in|out``); the row
    stores only metadata and the normalized on-disk byte sizes.
    """
    in_size, out_size = save_testcase_files(
        problem_id,
        ordinal,
        input_content.encode("utf-8"),
        output_content.encode("utf-8"),
        autojudge_settings.arena_testcase_dir,
    )
    tc = ArenaTestCase(
        problem_id=problem_id,
        ordinal=ordinal,
        input_size_bytes=in_size,
        output_size_bytes=out_size,
    )
    session.add(tc)
    return tc


def _make_language(session: AsyncSession, lang_id: str = "python3") -> Language:
    lang = Language(
        id=lang_id,
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


def _make_inactive_language(session: AsyncSession) -> Language:
    lang = Language(
        id="disabled-lang",
        name="Disabled",
        icon="",
        compile_image="img:compile",
        run_image="img:run",
        compile_cmd=None,
        run_cmd=["run"],
        source_filename="src",
        artifact_path="/sandbox/src",
        artifact_is_source=True,
        compile_timeout_s=5.0,
        active=False,
    )
    session.add(lang)
    return lang


def _make_arena_user(session: AsyncSession, *, role: ArenaRole = ArenaRole.ARENA_USER) -> ArenaUser:
    user = ArenaUser(
        nome="Arena Judge User",
        email_normalizado=f"arena-{uuid.uuid4().hex[:8]}@test.example.com",
        dta_nascimento=None,
        role=role,
    )
    user.password = "Senha@Forte1!"
    session.add(user)
    return user


def _make_submission(
    session: AsyncSession,
    problem: Problem,
    team: User,
    language: Language,
    source: str = "print('hello')",
) -> Submission:
    src_hash = hashlib.sha256(source.encode()).hexdigest()
    sub = Submission(
        problem_id=problem.id,
        team_id=team.id,
        language_id=language.id,
        source_code=source,
        source_hash=src_hash,
        source_size_bytes=len(source.encode()),
    )
    session.add(sub)
    return sub


def _make_judgment(
    session: AsyncSession,
    submission: Submission,
    status: JudgmentStatus = JudgmentStatus.QUEUED,
    attempt_token: str | None = TEST_ATTEMPT_TOKEN,
) -> SubmissionJudgment:
    j = SubmissionJudgment(
        submission_id=submission.id,
        status=status,
        attempt_token=attempt_token,
    )
    session.add(j)
    return j
