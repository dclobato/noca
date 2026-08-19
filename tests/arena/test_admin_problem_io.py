#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the Arena problem import/export service."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import zipfile
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings as arena_settings
from arena.models.arena_problems import ArenaCategory, ArenaProblemCustomValidator
from arena.models.arena_users import ArenaUser
from arena.services import admin_problem_io_service, admin_problem_service, admin_problem_tc_service
from shared.enumerations import (
    ArenaEditorialReleasePolicy,
    ArenaRole,
    CustomValidatorCandidateState,
    StatementLanguage,
)
from shared.services.imageprocessing_service import ImageProcessingService
from shared.services.problem_package import PackageError, read_problem_package
from shared.services.sample_problem_package import build_sample_problem_package as _write_sample_package
from shared.services.testcase_files import get_testcase_path
from web.models.language import Language

# The service now consumes an already-validated ProblemPackage and writes exports
# to a path. These shims keep the tests expressed in terms of bytes, which is what
# a hand-built fixture naturally produces.


async def _import_zip(
    session: AsyncSession,
    *,
    zip_bytes: bytes,
    caller_id: str,
    image_service: ImageProcessingService,
    testcase_dir: Path,
):
    """Read a package from bytes and import it, as the route does from an upload."""
    zip_path = Path(tempfile.mkdtemp(prefix="noca-test-pkg-")) / "package.zip"
    zip_path.write_bytes(zip_bytes)
    with read_problem_package(zip_path) as staged:
        return await admin_problem_io_service.import_problem_package(
            session,
            staged.package,
            caller_id=caller_id,
            image_service=image_service,
            testcase_dir=testcase_dir,
        )


def _export_zip(problem, owner_name: str, testcase_dir: Path, *, profile: str = "full") -> bytes:
    """Build a package and return its bytes."""
    destination = Path(tempfile.mkdtemp(prefix="noca-test-export-")) / "package.zip"
    admin_problem_io_service.export_problem_package(problem, owner_name, testcase_dir, destination, profile=profile)
    return destination.read_bytes()


def _fingerprint(zip_bytes: bytes) -> dict[str, object]:
    """Reduce package bytes to all metadata and normalized payloads."""
    path = Path(tempfile.mkdtemp(prefix="noca-test-fingerprint-")) / "package.zip"
    path.write_bytes(zip_bytes)
    with read_problem_package(path) as staged:
        package = staged.package
        return {
            "metadata": package.metadata,
            "statement": (
                package.statement.kind,
                package.statement.path.read_bytes() if package.statement.path else package.statement.text,
            ),
            "cases": [
                (
                    case.ordinal,
                    case.is_sample,
                    case.explanation,
                    case.input_path.read_bytes(),
                    case.output_path.read_bytes() if case.output_path else None,
                )
                for case in package.test_cases
            ],
            "image": (
                None
                if package.image is None
                else (
                    package.image.member,
                    package.image.path.read_bytes() if package.image.path else package.image.data,
                )
            ),
            "validator": package.validator,
            "interactions": package.interactions,
        }


def build_sample_problem_package() -> bytes:
    """Return the downloadable reference package as bytes."""
    destination = Path(tempfile.mkdtemp(prefix="noca-test-sample-")) / "sample.zip"
    return _write_sample_package(destination).read_bytes()


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


def _build_package(
    *,
    categories: list[str],
    time_limit_ms: int = 1000,
    memory_limit_kb: int = 262144,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "problem.json",
            json.dumps(
                {
                    "title": "Imported Problem",
                    "author": "Someone Else",
                    "source": "ICPC",
                    "time_limit_ms": time_limit_ms,
                    "memory_limit_kb": memory_limit_kb,
                    "pids_limit": 64,
                    "output_limit_in_bytes": 65536,
                    "categories": categories,
                    # Arena-specific optional metadata
                    "notes": "ignore me",
                    "license": "CC BY-SA 4.0",
                    # Contest-only keys: parsed and re-exported, but Arena has
                    # nowhere to store them.
                    "color": "#ff0000",
                    "language_limits": {"python": {"time_limit_ms": 2000, "memory_limit_kb": 262144, "pids_limit": 64}},
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
        await _import_zip(
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
    exported = _export_zip(
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

    reimported = (
        await _import_zip(
            session,
            zip_bytes=exported,
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )
    ).problem
    reloaded = await admin_problem_service.get_problem(session, reimported.id, caller_id=author.id, is_admin=False)
    assert reloaded is not None
    second = _export_zip(reloaded, author.nome, arena_settings.PROBLEM_TESTCASE_DIR)
    assert _fingerprint(second) == _fingerprint(exported)


@pytest.mark.asyncio
async def test_import_sets_author_secret_tcs_and_existing_categories(session: AsyncSession) -> None:
    author = await _make_author(session)
    session.add(ArenaCategory(name="Graphs", slug="graphs", color="#112233"))
    await session.commit()

    zip_bytes = _build_package(categories=["Graphs", "Does Not Exist"])
    problem = (
        await _import_zip(
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
async def test_import_accepts_the_shared_positive_limit_floor(session: AsyncSession) -> None:
    author = await _make_author(session)

    problem = (
        await _import_zip(
            session,
            zip_bytes=_build_package(categories=[], time_limit_ms=1, memory_limit_kb=1),
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )
    ).problem

    assert problem.time_limit_ms == 1
    assert problem.memory_limit_kb == 1


@pytest.mark.asyncio
async def test_export_round_trips_metadata(session: AsyncSession) -> None:
    author = await _make_author(session)
    session.add(ArenaCategory(name="Graphs", slug="graphs", color="#112233"))
    await session.commit()

    created = (
        await _import_zip(
            session,
            zip_bytes=_build_package(categories=["Graphs"]),
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )
    ).problem
    problem = await admin_problem_service.get_problem(session, created.id, caller_id=author.id, is_admin=False)
    assert problem is not None

    zip_bytes = _export_zip(problem, author.nome, arena_settings.PROBLEM_TESTCASE_DIR)
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
async def test_same_domain_round_trip_preserves_the_normalized_package(session: AsyncSession) -> None:
    author = await _make_author(session)

    first_problem = (
        await _import_zip(
            session,
            zip_bytes=_build_package(categories=[]),
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )
    ).problem
    first_loaded = await admin_problem_service.get_problem(
        session, first_problem.id, caller_id=author.id, is_admin=False
    )
    assert first_loaded is not None
    first = _export_zip(first_loaded, author.nome, arena_settings.PROBLEM_TESTCASE_DIR)

    second_problem = (
        await _import_zip(
            session,
            zip_bytes=first,
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )
    ).problem
    second_loaded = await admin_problem_service.get_problem(
        session, second_problem.id, caller_id=author.id, is_admin=False
    )
    assert second_loaded is not None
    second = _export_zip(second_loaded, author.nome, arena_settings.PROBLEM_TESTCASE_DIR)

    assert _fingerprint(second) == _fingerprint(first)


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
        await _import_zip(
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
    export = _export_zip(loaded, owner.nome, arena_settings.PROBLEM_TESTCASE_DIR)
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
        await _import_zip(
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
    export = _export_zip(problem, author.nome, arena_settings.PROBLEM_TESTCASE_DIR)
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
        await _import_zip(
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
async def test_import_defaults_a_missing_time_limit(session: AsyncSession) -> None:
    """An omitted limit takes the format's documented default, not an error."""
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
    result = await _import_zip(
        session,
        zip_bytes=zip_bytes,
        caller_id=author.id,
        image_service=ImageProcessingService(),
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )

    assert result.problem.time_limit_ms == 1000


@pytest.mark.asyncio
async def test_import_rejects_a_zero_time_limit(session: AsyncSession) -> None:
    """A stated but impossible limit is still an error, quoting the value."""
    author = await _make_author(session)
    zip_bytes = _build_raw_package(
        {
            "problem.json": json.dumps({**_VALID_META, "time_limit_ms": 0}),
            "statement.md": "# X\n\nbody\n",
            "in/001.in": "1\n",
            "out/001.out": "1\n",
        }
    )
    with pytest.raises(ValueError, match="'time_limit_ms' must be an integer >= 1"):
        await _import_zip(
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
        await _import_zip(
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
        await _import_zip(
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
        await _import_zip(
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
        await _import_zip(
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
        await _import_zip(
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
    # The package's sample_testcases decides which cases are public, so the
    # sample's one worked example arrives public and the rest stay secret.
    assert [tc.is_sample for tc in test_cases] == [True, False, False]
    assert {category.name for category in problem.categories} == {"sample", "math"}


# ── Statement language ────────────────────────────────────────────────────────

_PT_STATEMENT = (
    "# Soma de dois números\n\n"
    "Dado dois números inteiros, escreva um programa que calcule a soma deles e\n"
    "imprima o resultado na saída padrão do seu programa.\n"
)


def _language_package(language: str | None, statement: str = _PT_STATEMENT) -> bytes:
    meta = dict(_VALID_META)
    if language is not None:
        meta["statement_language"] = language
    return _build_raw_package(
        {
            "problem.json": json.dumps(meta),
            "statement.md": statement,
            "in/001.in": "1\n",
            "out/001.out": "1\n",
        }
    )


@pytest.mark.asyncio
async def test_import_uses_the_stated_statement_language(session: AsyncSession) -> None:
    """A stated language is authoritative, even when detection would disagree."""
    author = await _make_author(session)

    result = await _import_zip(
        session,
        zip_bytes=_language_package("en"),
        caller_id=author.id,
        image_service=ImageProcessingService(),
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )

    assert result.problem.statement_language == StatementLanguage.EN
    assert result.statement_language == StatementLanguage.EN
    assert result.language_source == "package"


@pytest.mark.asyncio
async def test_import_detects_a_missing_statement_language(session: AsyncSession) -> None:
    author = await _make_author(session)

    result = await _import_zip(
        session,
        zip_bytes=_language_package(None),
        caller_id=author.id,
        image_service=ImageProcessingService(),
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )

    assert result.problem.statement_language == StatementLanguage.PT
    assert result.language_source == "detected"


@pytest.mark.asyncio
async def test_import_reports_an_undeterminable_statement_language(session: AsyncSession) -> None:
    """Too little text to detect is its own outcome, not a silent success."""
    author = await _make_author(session)

    result = await _import_zip(
        session,
        zip_bytes=_language_package(None, statement="# X\n\nOi.\n"),
        caller_id=author.id,
        image_service=ImageProcessingService(),
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )

    assert result.problem.statement_language is None
    assert result.language_source == "undetermined"


@pytest.mark.asyncio
async def test_import_rejects_an_unsupported_statement_language(session: AsyncSession) -> None:
    author = await _make_author(session)

    with pytest.raises(ValueError, match="statement_language"):
        await _import_zip(
            session,
            zip_bytes=_language_package("klingon"),
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )


@pytest.mark.asyncio
async def test_export_writes_a_null_statement_language_when_unset(session: AsyncSession) -> None:
    author = await _make_author(session)
    created = (
        await _import_zip(
            session,
            zip_bytes=_language_package(None, statement="# X\n\nOi.\n"),
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )
    ).problem
    problem = await admin_problem_service.get_problem(session, created.id, caller_id=author.id, is_admin=False)
    assert problem is not None

    zip_bytes = _export_zip(problem, author.nome, arena_settings.PROBLEM_TESTCASE_DIR)
    meta = json.loads(zipfile.ZipFile(io.BytesIO(zip_bytes)).read("problem.json").decode("utf-8"))

    # Every version-1 key is always written; an unset language is an explicit null
    # rather than an absent key a consumer would have to guess about.
    assert meta["statement_language"] is None


@pytest.mark.asyncio
async def test_statement_language_survives_an_export_import_round_trip(session: AsyncSession) -> None:
    author = await _make_author(session)
    created = (
        await _import_zip(
            session,
            zip_bytes=_language_package("es"),
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )
    ).problem
    problem = await admin_problem_service.get_problem(session, created.id, caller_id=author.id, is_admin=False)
    assert problem is not None

    exported = _export_zip(problem, author.nome, arena_settings.PROBLEM_TESTCASE_DIR)
    meta = json.loads(zipfile.ZipFile(io.BytesIO(exported)).read("problem.json").decode("utf-8"))
    assert meta["statement_language"] == "es"

    reimported = await _import_zip(
        session,
        zip_bytes=exported,
        caller_id=author.id,
        image_service=ImageProcessingService(),
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )
    assert reimported.problem.statement_language == StatementLanguage.ES
    assert reimported.language_source == "package"


@pytest.mark.asyncio
async def test_import_refuses_a_validator_whose_language_is_not_active(session: AsyncSession) -> None:
    """Language availability is a target question the shared reader cannot answer."""
    author = await _make_author(session)
    await session.commit()

    with pytest.raises(ValueError, match="is not active on this platform"):
        await _import_zip(
            session,
            zip_bytes=_build_validator_package(),
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )


# ── Editorial release policy ──────────────────────────────────────────────────


def _editorial_package(release_policy: str | None) -> bytes:
    """Build a package carrying an editorial, optionally stating its policy."""
    editorial = "# Editorial\n\nAdd the two values.\n"
    declaration: dict[str, object] = {
        "member": "editorial.md",
        "sha256": hashlib.sha256(editorial.encode("utf-8")).hexdigest(),
    }
    if release_policy is not None:
        declaration["release_policy"] = release_policy
    meta = dict(_VALID_META)
    meta["editorial"] = declaration
    return _build_raw_package(
        {
            "problem.json": json.dumps(meta),
            "statement.md": "# X\n\nbody\n",
            "editorial.md": editorial,
            "in/001.in": "1\n",
            "out/001.out": "1\n",
        }
    )


@pytest.mark.asyncio
async def test_editorial_release_policy_survives_an_export_import_round_trip(session: AsyncSession) -> None:
    """The regression: an exported policy must not silently revert to 'never'.

    An editorial that imports as ``never`` is invisible to participants, so
    losing the policy quietly undoes the author's decision on the other install.
    """
    author = await _make_author(session)
    created = (
        await _import_zip(
            session,
            zip_bytes=_editorial_package("after_ac"),
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )
    ).problem
    assert created.editorial_release_policy is ArenaEditorialReleasePolicy.AFTER_AC

    problem = await admin_problem_service.get_problem(session, created.id, caller_id=author.id, is_admin=False)
    assert problem is not None

    exported = _export_zip(problem, author.nome, arena_settings.PROBLEM_TESTCASE_DIR)
    meta = json.loads(zipfile.ZipFile(io.BytesIO(exported)).read("problem.json").decode("utf-8"))
    assert meta["editorial"]["release_policy"] == "after_ac"

    reimported = await _import_zip(
        session,
        zip_bytes=exported,
        caller_id=author.id,
        image_service=ImageProcessingService(),
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )
    assert reimported.problem.editorial_release_policy is ArenaEditorialReleasePolicy.AFTER_AC


@pytest.mark.asyncio
async def test_a_package_without_a_release_policy_imports_as_never(session: AsyncSession) -> None:
    """Packages written before the property existed keep their old behavior."""
    author = await _make_author(session)

    result = await _import_zip(
        session,
        zip_bytes=_editorial_package(None),
        caller_id=author.id,
        image_service=ImageProcessingService(),
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )

    assert result.problem.editorial is not None
    assert result.problem.editorial_release_policy is ArenaEditorialReleasePolicy.NEVER


@pytest.mark.asyncio
async def test_import_refuses_an_unknown_release_policy(session: AsyncSession) -> None:
    author = await _make_author(session)

    with pytest.raises(PackageError, match="release_policy"):
        await _import_zip(
            session,
            zip_bytes=_editorial_package("someday"),
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )


@pytest.mark.asyncio
async def test_a_problem_without_an_editorial_exports_a_null_editorial(session: AsyncSession) -> None:
    """The policy is nested, so no editorial means no object to carry it."""
    author = await _make_author(session)
    created = (
        await _import_zip(
            session,
            zip_bytes=_language_package("en"),
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )
    ).problem
    problem = await admin_problem_service.get_problem(session, created.id, caller_id=author.id, is_admin=False)
    assert problem is not None

    exported = _export_zip(problem, author.nome, arena_settings.PROBLEM_TESTCASE_DIR)
    meta = json.loads(zipfile.ZipFile(io.BytesIO(exported)).read("problem.json").decode("utf-8"))

    assert meta["editorial"] is None
