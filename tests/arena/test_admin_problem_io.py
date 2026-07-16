#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the Arena problem import/export service."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings as arena_settings
from arena.models.arena_problems import ArenaCategory, ArenaProblemCustomValidator
from arena.models.arena_users import ArenaUser
from arena.services import admin_problem_io_service, admin_problem_service, admin_problem_tc_service
from shared.enumerations import ArenaRole, CustomValidatorCandidateState
from shared.services.imageprocessing_service import ImageProcessingService
from shared.services.sample_problem_package import build_sample_problem_package
from shared.services.testcase_files import get_testcase_path
from web.models.language import Language


async def _make_author(session: AsyncSession) -> ArenaUser:
    user = ArenaUser(
        nome="Judge Author",
        email_normalizado="judge@test.example",
        password_hash="hash",
        role=ArenaRole.ARENA_JUDGE,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        session_version=0,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _build_package(*, categories: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "problem.json",
            json.dumps(
                {
                    "title": "Imported Problem",
                    "author": "Someone Else",
                    "source": "ICPC",
                    "time_limit_ms": 1000,
                    "memory_limit_kb": 262144,
                    "pids_limit": 64,
                    "output_limit_in_bytes": 65536,
                    "categories": categories,
                    # Arena-specific optional metadata
                    "notes": "ignore me",
                    "license": "CC BY-SA 4.0",
                    # Web-only keys must be ignored.
                    "color": "#ff0000",
                    "language_limits": {"python": {"time_limit_ms": 2000}},
                }
            ),
        )
        archive.writestr("statement.md", "# Title\n\nDo the thing.\n")
        archive.writestr("in/001.in", "1 2\n")
        archive.writestr("out/001.out", "3\n")
        archive.writestr("in/002.in", "4 5\n")
        archive.writestr("out/002.out", "9\n")
    return buffer.getvalue()


def _build_validator_package() -> bytes:
    """A validator package: its cases parametrize the validator, so inputs only."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "problem.json",
            json.dumps(
                {
                    **_VALID_META,
                    "custom_validator": {
                        "language_id": "python3",
                        "source_file": "validator/source.txt",
                    },
                }
            ),
        )
        archive.writestr("statement.md", "# Interactive\n\nInputs only.\n")
        archive.writestr("validator/source.txt", "print('ready')\n")
        archive.writestr("in/001.in", "example\n")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_validator_package_import_is_pending_and_disabled(session: AsyncSession) -> None:
    author = await _make_author(session)
    session.add(
        Language(
            id="python3",
            name="Python 3",
            icon="python",
            compile_image="compile",
            run_image="run",
            compile_cmd=["true"],
            run_cmd=["python3", "/sandbox/source.py"],
            source_filename="source.py",
            artifact_path="/sandbox/source.py",
            artifact_is_source=True,
            compile_timeout_s=10,
            active=True,
        )
    )
    await session.commit()

    problem = (
        await admin_problem_io_service.import_problem_from_zip(
            session,
            zip_bytes=_build_validator_package(),
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )
    ).problem

    validator = await session.get(ArenaProblemCustomValidator, problem.id)
    test_cases = await admin_problem_tc_service.list_testcases(session, problem.id)
    assert problem.enabled is False
    assert validator is not None
    assert validator.candidate_state == CustomValidatorCandidateState.PENDING
    # The case carries input only, and is secret like any other imported case.
    assert [(tc.ordinal, tc.is_sample, tc.output_size_bytes) for tc in test_cases] == [(1, False, None)]

    loaded = await admin_problem_service.get_problem(
        session,
        problem.id,
        caller_id=author.id,
        is_admin=False,
    )
    assert loaded is not None
    stale_out = get_testcase_path(problem.id, 1, "out", arena_settings.PROBLEM_TESTCASE_DIR)
    stale_out.write_bytes(b"stale\n")
    exported = admin_problem_io_service.build_export_zip(
        loaded,
        author.nome,
        arena_settings.PROBLEM_TESTCASE_DIR,
    )
    with zipfile.ZipFile(io.BytesIO(exported)) as archive:
        assert archive.read("validator/validator.py") == b"print('ready')\n"
        assert archive.read("in/001.in") == b"example\n"
        # An interactive problem has no expected output to ship, even if a stale
        # legacy .out file is still present on disk.
        assert "out/001.out" not in archive.namelist()


@pytest.mark.asyncio
async def test_import_sets_author_secret_tcs_and_existing_categories(session: AsyncSession) -> None:
    author = await _make_author(session)
    session.add(ArenaCategory(name="Graphs", slug="graphs", color="#112233"))
    await session.commit()

    zip_bytes = _build_package(categories=["Graphs", "Does Not Exist"])
    problem = (
        await admin_problem_io_service.import_problem_from_zip(
            session,
            zip_bytes=zip_bytes,
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )
    ).problem

    assert problem.owner_id == author.id
    assert problem.enabled is False
    assert problem.author == "Someone Else"
    assert problem.author_is_owner is False
    assert problem.source == "ICPC"
    assert problem.notes == "ignore me"
    assert problem.license == "CC BY-SA 4.0"

    test_cases = await admin_problem_tc_service.list_testcases(session, problem.id)
    assert len(test_cases) == 2
    assert all(tc.is_sample is False for tc in test_cases)

    reloaded = await admin_problem_service.get_problem(session, problem.id, caller_id=author.id, is_admin=False)
    assert reloaded is not None
    category_names = {cat.name for cat in reloaded.categories}
    assert category_names == {"Graphs"}  # unknown category dropped


@pytest.mark.asyncio
async def test_export_round_trips_metadata(session: AsyncSession) -> None:
    author = await _make_author(session)
    session.add(ArenaCategory(name="Graphs", slug="graphs", color="#112233"))
    await session.commit()

    created = (
        await admin_problem_io_service.import_problem_from_zip(
            session,
            zip_bytes=_build_package(categories=["Graphs"]),
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )
    ).problem
    problem = await admin_problem_service.get_problem(session, created.id, caller_id=author.id, is_admin=False)
    assert problem is not None

    zip_bytes = admin_problem_io_service.build_export_zip(problem, author.nome, arena_settings.PROBLEM_TESTCASE_DIR)
    archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    meta = json.loads(archive.read("problem.json").decode("utf-8"))

    assert meta["title"] == "Imported Problem"
    assert meta["author"] == "Someone Else"
    assert meta["source"] == "ICPC"
    assert meta["license"] == "CC BY-SA 4.0"
    assert meta["categories"] == ["Graphs"]  # list of strings
    assert "statement.md" in archive.namelist()
    assert "in/001.in" in archive.namelist()
    assert "out/002.out" in archive.namelist()


@pytest.mark.asyncio
async def test_import_without_author_uses_owner_name_on_export(session: AsyncSession) -> None:
    owner = await _make_author(session)
    zip_bytes = _build_raw_package(
        {
            "problem.json": json.dumps(_VALID_META),
            "statement.md": "# X\n\nbody\n",
            "in/001.in": "1\n",
            "out/001.out": "1\n",
        }
    )

    problem = (
        await admin_problem_io_service.import_problem_from_zip(
            session,
            zip_bytes=zip_bytes,
            caller_id=owner.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )
    ).problem
    assert problem.author is None
    assert problem.author_is_owner is True

    loaded = await admin_problem_service.get_problem(
        session,
        problem.id,
        caller_id=owner.id,
        is_admin=False,
    )
    assert loaded is not None
    export = admin_problem_io_service.build_export_zip(loaded, owner.nome, arena_settings.PROBLEM_TESTCASE_DIR)
    meta = json.loads(zipfile.ZipFile(io.BytesIO(export)).read("problem.json"))
    assert meta["author"] == owner.nome
    assert meta["license"] is None


@pytest.mark.asyncio
async def test_import_export_round_trips_explanations(session: AsyncSession) -> None:
    author = await _make_author(session)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("problem.json", json.dumps(_VALID_META))
        archive.writestr("statement.md", "# X\n\nbody\n")
        archive.writestr("in/001.in", "1\n")
        archive.writestr("out/001.out", "1\n")
        archive.writestr("explanation/001.txt", "because one")
        archive.writestr("in/002.in", "2\n")
        archive.writestr("out/002.out", "2\n")  # second case has no explanation

    created = (
        await admin_problem_io_service.import_problem_from_zip(
            session,
            zip_bytes=buffer.getvalue(),
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )
    ).problem

    test_cases = await admin_problem_tc_service.list_testcases(session, created.id)
    by_ordinal = {tc.ordinal: tc for tc in test_cases}
    assert by_ordinal[1].explanation == "because one"
    assert by_ordinal[2].explanation is None

    problem = await admin_problem_service.get_problem(session, created.id, caller_id=author.id, is_admin=False)
    assert problem is not None
    export = admin_problem_io_service.build_export_zip(problem, author.nome, arena_settings.PROBLEM_TESTCASE_DIR)
    out_archive = zipfile.ZipFile(io.BytesIO(export))
    assert out_archive.read("explanation/001.txt").decode("utf-8") == "because one"
    assert "explanation/002.txt" not in out_archive.namelist()


@pytest.mark.asyncio
async def test_import_rejects_missing_statement(session: AsyncSession) -> None:
    author = await _make_author(session)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "problem.json",
            json.dumps({"title": "X", "time_limit_ms": 1000, "memory_limit_kb": 262144, "pids_limit": 64}),
        )
        archive.writestr("in/001.in", "1\n")
        archive.writestr("out/001.out", "1\n")

    with pytest.raises(ValueError, match="statement.md"):
        await admin_problem_io_service.import_problem_from_zip(
            session,
            zip_bytes=buffer.getvalue(),
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )


def _build_raw_package(entries: dict[str, bytes | str]) -> bytes:
    """Build a ZIP from an explicit name → contents mapping."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, contents in entries.items():
            archive.writestr(name, contents)
    return buffer.getvalue()


_VALID_META = {
    "title": "X",
    "time_limit_ms": 1000,
    "memory_limit_kb": 262144,
    "pids_limit": 64,
}


@pytest.mark.asyncio
async def test_import_requires_time_limit(session: AsyncSession) -> None:
    author = await _make_author(session)
    meta = {key: value for key, value in _VALID_META.items() if key != "time_limit_ms"}
    zip_bytes = _build_raw_package(
        {
            "problem.json": json.dumps(meta),
            "statement.md": "# X\n\nbody\n",
            "in/001.in": "1\n",
            "out/001.out": "1\n",
        }
    )
    with pytest.raises(ValueError, match="time_limit_ms"):
        await admin_problem_io_service.import_problem_from_zip(
            session,
            zip_bytes=zip_bytes,
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )


@pytest.mark.asyncio
async def test_import_rejects_unpaired_test_cases(session: AsyncSession) -> None:
    author = await _make_author(session)
    zip_bytes = _build_raw_package(
        {
            "problem.json": json.dumps(_VALID_META),
            "statement.md": "# X\n\nbody\n",
            "in/001.in": "1\n",  # no matching out/001.out
        }
    )
    with pytest.raises(ValueError, match="without matching output"):
        await admin_problem_io_service.import_problem_from_zip(
            session,
            zip_bytes=zip_bytes,
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )


@pytest.mark.asyncio
async def test_import_rejects_oversized_image(session: AsyncSession) -> None:
    author = await _make_author(session)
    oversized = b"\x00" * (2 * 1024 * 1024 + 1)  # exceeds the 2 MB cap before any decode
    zip_bytes = _build_raw_package(
        {
            "problem.json": json.dumps({**_VALID_META, "image": "image.png"}),
            "statement.md": "# X\n\nbody\n",
            "image.png": oversized,
            "in/001.in": "1\n",
            "out/001.out": "1\n",
        }
    )
    with pytest.raises(ValueError, match="Invalid problem image"):
        await admin_problem_io_service.import_problem_from_zip(
            session,
            zip_bytes=zip_bytes,
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )


@pytest.mark.asyncio
async def test_import_rejects_missing_referenced_image(session: AsyncSession) -> None:
    author = await _make_author(session)
    zip_bytes = _build_raw_package(
        {
            "problem.json": json.dumps({**_VALID_META, "image": "image.png"}),  # not in the ZIP
            "statement.md": "# X\n\nbody\n",
            "in/001.in": "1\n",
            "out/001.out": "1\n",
        }
    )
    with pytest.raises(ValueError, match="not present in the ZIP"):
        await admin_problem_io_service.import_problem_from_zip(
            session,
            zip_bytes=zip_bytes,
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )


@pytest.mark.asyncio
async def test_import_rejects_binary_test_case(session: AsyncSession) -> None:
    author = await _make_author(session)
    zip_bytes = _build_raw_package(
        {
            "problem.json": json.dumps(_VALID_META),
            "statement.md": "# X\n\nbody\n",
            "in/001.in": b"\xff\xfe\x00\x01",  # invalid UTF-8
            "out/001.out": "1\n",
        }
    )
    with pytest.raises(ValueError, match="not valid UTF-8"):
        await admin_problem_io_service.import_problem_from_zip(
            session,
            zip_bytes=zip_bytes,
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )


@pytest.mark.asyncio
async def test_sample_package_imports_cleanly_into_arena(session: AsyncSession) -> None:
    """The same package the import page offers must import on the Arena side too.

    It carries Contest-only keys (`color`, `language_limits`); the Arena importer
    must ignore them rather than fail.
    """
    author = await _make_author(session)
    session.add_all([ArenaCategory(name="sample", slug="sample"), ArenaCategory(name="math", slug="math")])
    await session.commit()

    problem = (
        await admin_problem_io_service.import_problem_from_zip(
            session,
            zip_bytes=build_sample_problem_package(),
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )
    ).problem

    test_cases = await admin_problem_tc_service.list_testcases(session, problem.id)
    await session.refresh(problem, attribute_names=["categories"])
    assert problem.title == "A + B"
    assert problem.author == "John Doe"
    assert problem.notes == "Sample problem"
    assert problem.license == "cc sa-by"
    assert problem.time_limit_ms == 1000
    assert problem.enabled is False
    assert [tc.ordinal for tc in test_cases] == [1, 2, 3]
    assert all(not tc.is_sample for tc in test_cases)
    assert {category.name for category in problem.categories} == {"sample", "math"}
