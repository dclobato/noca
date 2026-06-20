#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for admin_problem_tc_service (filesystem-backed CRUD + ZIP replace)."""

import io
import zipfile
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.config import settings
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_users import ArenaUser
from arena.services import admin_problem_service, admin_problem_tc_service
from shared.enumerations import ArenaRole
from shared.services.testcase_files import read_testcase_full
from shared.tc_zip import MAX_INLINE_TESTCASE_BYTES

_TC_DIR = settings.PROBLEM_TESTCASE_DIR


def _full(problem_id: str, ordinal: int) -> tuple[str, str]:
    """Read the on-disk content of one test-case pair."""
    return read_testcase_full(problem_id, ordinal, _TC_DIR)


async def _make_user(session: AsyncSession) -> ArenaUser:
    user = ArenaUser(
        nome="TC Author",
        email_normalizado="tc-author@test.example",
        password_hash="hash",
        role=ArenaRole.ARENA_JUDGE,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(1990, 1, 1),
        consentimento_responsavel=True,
        session_version=0,
    )
    session.add(user)
    await session.flush()
    return user


async def _make_problem(session: AsyncSession, owner_id: str) -> ArenaProblem:
    p = await admin_problem_service.create_problem(
        session,
        caller_id=owner_id,
        title="TC Test Problem",
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="Statement",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[],
    )
    await session.flush()
    return p


def _make_zip(pairs: dict[int, tuple[str, str]]) -> bytes:
    """Build an in-memory ZIP in directory layout (in/NNN.in, out/NNN.out)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for ordinal, (inp, out) in pairs.items():
            archive.writestr(f"in/{ordinal:03d}.in", inp)
            archive.writestr(f"out/{ordinal:03d}.out", out)
    return buf.getvalue()


async def _create(session: AsyncSession, problem: ArenaProblem, inp: str, out: str, *, is_sample: bool = False):  # type: ignore[no-untyped-def]
    """Create a test case and immediately write its files (simulates post-commit call)."""
    tc, write_files = await admin_problem_tc_service.create_testcase(
        session, problem, input_content=inp, output_content=out, is_sample=is_sample, testcase_dir=_TC_DIR
    )
    write_files()
    return tc


# ── create_testcase ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_testcase_assigns_ordinal(session: AsyncSession) -> None:
    author = await _make_user(session)
    problem = await _make_problem(session, author.id)
    tc1 = await _create(session, problem, "1", "1", is_sample=True)
    tc2 = await _create(session, problem, "2", "2")
    await session.flush()
    assert tc1.ordinal == 1
    assert tc2.ordinal == 2
    assert tc1.input_size_bytes == 1


@pytest.mark.asyncio
async def test_create_testcase_sample_flag(session: AsyncSession) -> None:
    author = await _make_user(session)
    problem = await _make_problem(session, author.id)
    tc = await _create(session, problem, "in", "out", is_sample=True)
    await session.flush()
    assert tc.is_sample is True
    assert _full(problem.id, tc.ordinal) == ("in", "out")


@pytest.mark.asyncio
async def test_create_testcase_rejects_oversize_inline(session: AsyncSession) -> None:
    author = await _make_user(session)
    problem = await _make_problem(session, author.id)
    big = "x" * (MAX_INLINE_TESTCASE_BYTES + 1)
    with pytest.raises(ValueError, match="inline-edit limit"):
        await admin_problem_tc_service.create_testcase(
            session, problem, input_content=big, output_content="1", is_sample=False, testcase_dir=_TC_DIR
        )


# ── update_testcase ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_testcase_changes_content(session: AsyncSession) -> None:
    author = await _make_user(session)
    problem = await _make_problem(session, author.id)
    tc = await _create(session, problem, "old_in", "old_out")
    await session.flush()

    _tc, write_files = await admin_problem_tc_service.update_testcase(
        session, tc, input_content="new_in", output_content="new_out", is_sample=True, testcase_dir=_TC_DIR
    )
    write_files()
    assert _full(problem.id, tc.ordinal) == ("new_in", "new_out")
    assert tc.is_sample is True


# ── replace_single_testcase ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_replace_single_testcase_no_cap(session: AsyncSession) -> None:
    author = await _make_user(session)
    problem = await _make_problem(session, author.id)
    tc = await _create(session, problem, "x", "y")
    await session.flush()

    big = ("z" * (MAX_INLINE_TESTCASE_BYTES + 100)).encode()
    _tc, write_files = await admin_problem_tc_service.replace_single_testcase(
        session, tc, input_bytes=big, output_bytes=b"out", explanation="why", testcase_dir=_TC_DIR
    )
    write_files()
    assert tc.input_size_bytes == len(big)
    assert tc.explanation == "why"


# ── toggle_sample ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_toggle_sample_flips_flag_without_touching_content(session: AsyncSession) -> None:
    author = await _make_user(session)
    problem = await _make_problem(session, author.id)
    tc = await _create(session, problem, "in", "out")
    await session.flush()

    await admin_problem_tc_service.toggle_sample(session, tc)
    assert tc.is_sample is True
    await admin_problem_tc_service.toggle_sample(session, tc)
    assert tc.is_sample is False
    assert _full(problem.id, tc.ordinal) == ("in", "out")


# ── delete_testcase + renumber ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_testcase_renumbers_remaining(session: AsyncSession) -> None:
    author = await _make_user(session)
    problem = await _make_problem(session, author.id)

    tc1 = await _create(session, problem, "a", "a", is_sample=True)
    await _create(session, problem, "b", "b")
    await _create(session, problem, "c", "c")
    await session.commit()

    tc1_id = tc1.id
    cleanup_files = await admin_problem_tc_service.delete_testcase(session, tc1, testcase_dir=_TC_DIR)
    await session.commit()
    cleanup_files()

    remaining = await admin_problem_tc_service.list_testcases(session, problem.id)
    assert len(remaining) == 2
    assert [tc.ordinal for tc in remaining] == [1, 2]
    assert all(tc.id != tc1_id for tc in remaining)
    # Files track ordinals: ordinal 1 now holds "b", ordinal 2 holds "c".
    assert _full(problem.id, 1) == ("b", "b")
    assert _full(problem.id, 2) == ("c", "c")


# ── move_testcase + renumber ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_move_testcase_reorders_and_renumbers(session: AsyncSession) -> None:
    author = await _make_user(session)
    problem = await _make_problem(session, author.id)

    tc1 = await _create(session, problem, "a", "a", is_sample=True)
    tc2 = await _create(session, problem, "b", "b")
    tc3 = await _create(session, problem, "c", "c")
    await session.flush()

    await admin_problem_tc_service.move_testcase(session, tc3, 1, testcase_dir=_TC_DIR)
    await session.flush()

    reordered = await admin_problem_tc_service.list_testcases(session, problem.id)
    assert [(tc.id, tc.ordinal) for tc in reordered] == [
        (tc3.id, 1),
        (tc1.id, 2),
        (tc2.id, 3),
    ]
    # Content moved with the ordinals.
    assert _full(problem.id, 1) == ("c", "c")
    assert _full(problem.id, 2) == ("a", "a")
    assert _full(problem.id, 3) == ("b", "b")


# ── replace_all_from_zip ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_zip_replace_deletes_old_and_inserts_new(session: AsyncSession) -> None:
    author = await _make_user(session)
    problem = await _make_problem(session, author.id)

    await _create(session, problem, "old", "old_out", is_sample=True)
    await session.flush()

    zip_bytes = _make_zip({1: ("new_in_1", "new_out_1"), 2: ("new_in_2", "new_out_2")})
    count, apply_files = await admin_problem_tc_service.replace_all_from_zip(
        session, problem, zip_bytes, testcase_dir=_TC_DIR
    )
    await session.flush()
    apply_files()

    assert count == 2
    tcs = await admin_problem_tc_service.list_testcases(session, problem.id)
    assert len(tcs) == 2
    assert _full(problem.id, 1)[0] == "new_in_1"
    assert _full(problem.id, 2)[0] == "new_in_2"


@pytest.mark.asyncio
async def test_zip_replace_invalid_zip_raises(session: AsyncSession) -> None:
    author = await _make_user(session)
    problem = await _make_problem(session, author.id)
    with pytest.raises(ValueError, match="Invalid ZIP"):
        await admin_problem_tc_service.replace_all_from_zip(session, problem, b"not a zip", testcase_dir=_TC_DIR)


@pytest.mark.asyncio
async def test_zip_replace_empty_zip_raises(session: AsyncSession) -> None:
    author = await _make_user(session)
    problem = await _make_problem(session, author.id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("in/001.in", "input only")
    with pytest.raises(ValueError):
        await admin_problem_tc_service.replace_all_from_zip(session, problem, buf.getvalue(), testcase_dir=_TC_DIR)


# ── CRLF normalization ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_testcase_normalizes_crlf(session: AsyncSession) -> None:
    author = await _make_user(session)
    problem = await _make_problem(session, author.id)
    tc = await _create(session, problem, "line1\r\nline2\r\n", "out\r\n")
    await session.flush()
    assert _full(problem.id, tc.ordinal) == ("line1\nline2\n", "out\n")


@pytest.mark.asyncio
async def test_create_testcase_normalizes_lone_cr(session: AsyncSession) -> None:
    author = await _make_user(session)
    problem = await _make_problem(session, author.id)
    tc = await _create(session, problem, "P\rQ\r", "YES\r")
    await session.flush()
    assert _full(problem.id, tc.ordinal) == ("P\nQ\n", "YES\n")


@pytest.mark.asyncio
async def test_zip_replace_normalizes_crlf(session: AsyncSession) -> None:
    author = await _make_user(session)
    problem = await _make_problem(session, author.id)

    zip_bytes = _make_zip({1: ("P\r\n", "YES\r\n"), 2: ("Q\r\n", "NO\r\n")})
    _count, apply_files = await admin_problem_tc_service.replace_all_from_zip(
        session, problem, zip_bytes, testcase_dir=_TC_DIR
    )
    await session.flush()
    apply_files()

    assert _full(problem.id, 1) == ("P\n", "YES\n")
    assert _full(problem.id, 2) == ("Q\n", "NO\n")
