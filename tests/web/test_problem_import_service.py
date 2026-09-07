#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import io
import json
import os
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import contest_languages as contest_languages_table
from shared.enumerations import CustomValidatorCandidateState
from shared.services.imageprocessing_service import ImageProcessingService
from shared.services.problem_package import read_problem_package
from shared.services.sample_problem_package import build_sample_problem_package
from shared.services.testcase_files import get_testcase_path
from web.config import settings
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import ProblemCustomValidator, ProblemLanguageLimit, ProblemTestCase
from web.services.problem_service import (
    BALLOON_COLORS,
    build_problem_export,
    get_language_limits_map,
    get_problem_in_contest,
    import_problem_package,
)
from web.services.problem_service.importing import _pick_balloon_color


async def import_problem_from_zip(
    session: AsyncSession,
    contest: Contest,
    zip_bytes: bytes,
    testcase_dir,
    statement_dir,
    image_service: ImageProcessingService,
):
    """Read a package from bytes and import it, as the route does from an upload."""
    zip_path = Path(tempfile.mkdtemp(prefix="noca-test-pkg-")) / "package.zip"
    zip_path.write_bytes(zip_bytes)
    with read_problem_package(zip_path) as staged:
        return await import_problem_package(
            session, contest, staged.package, testcase_dir, statement_dir, image_service
        )


def export_bytes(problem, language_limits=None, *, profile="full") -> bytes:
    """Build a package and return its bytes, so assertions stay readable."""
    destination = Path(tempfile.mkdtemp(prefix="noca-test-export-")) / "package.zip"
    build_problem_export(
        problem,
        settings.PROBLEM_TESTCASE_DIR,
        settings.PROBLEM_STATEMENT_DIR,
        destination,
        profile=profile,
        language_limits=language_limits,
    )
    return destination.read_bytes()


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
                "repetitions": 2,
            },
            "java": {
                "time_limit_ms": "2000",
                "memory_limit_kb": "262144",
                "pids_limit": "64",
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
        ImageProcessingService(),
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

    # A hand-built package carries no sha256 manifest, which is its own warning.
    codes = {warning.code for warning in result.warnings}
    assert codes == {"integrity_manifest_missing", "disallowed_language_limits"}
    skipped = next(w for w in result.warnings if w.code == "disallowed_language_limits")
    assert java.id in skipped.message
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


def _validator_problem_zip() -> bytes:
    """A validator package: its cases parametrize the validator, so inputs only."""
    payload = {
        "title": "Interactive Problem",
        "time_limit_ms": 1000,
        "memory_limit_kb": 262144,
        "pids_limit": 64,
        "custom_validator": {
            "language_id": "python3",
            "source_file": "validator/validator.py",
        },
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("problem.json", json.dumps(payload))
        archive.writestr("statement.md", "# Interactive\n\nTalk to the validator.\n")
        archive.writestr("validator/validator.py", "print('ready')\n")
        archive.writestr("in/001.in", "example\n")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_validator_package_import_stages_candidate_with_input_only_cases(
    session: AsyncSession,
    uberadmin,
) -> None:
    await _make_language(session, "python3", "Python 3")
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.commit()

    result = await import_problem_from_zip(
        session,
        contest,
        _validator_problem_zip(),
        settings.PROBLEM_TESTCASE_DIR,
        settings.PROBLEM_STATEMENT_DIR,
        ImageProcessingService(),
    )

    validator = await session.get(ProblemCustomValidator, result.problem.id)
    test_cases = list(
        await session.scalars(select(ProblemTestCase).where(ProblemTestCase.problem_id == result.problem.id))
    )
    assert validator is not None
    assert validator.candidate_state == CustomValidatorCandidateState.PENDING
    assert validator.active_source is None
    assert result.validator_candidate_token == validator.candidate_token
    # The case carries input only, and is secret like any other imported case.
    assert [(tc.ordinal, tc.is_sample, tc.output_size_bytes) for tc in test_cases] == [(1, False, None)]


@pytest.mark.asyncio
async def test_validator_full_export_includes_candidate_but_public_export_omits_it(
    session: AsyncSession,
    uberadmin,
) -> None:
    await _make_language(session, "python3", "Python 3")
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.commit()
    imported = await import_problem_from_zip(
        session,
        contest,
        _validator_problem_zip(),
        settings.PROBLEM_TESTCASE_DIR,
        settings.PROBLEM_STATEMENT_DIR,
        ImageProcessingService(),
    )
    problem = await get_problem_in_contest(session, contest, imported.problem.id)
    assert problem is not None
    stale_out = get_testcase_path(problem.id, 1, "out", settings.PROBLEM_TESTCASE_DIR)
    stale_out.write_bytes(b"stale\n")

    full = export_bytes(problem, await get_language_limits_map(session, problem))
    public = export_bytes(problem, profile="public")

    with zipfile.ZipFile(io.BytesIO(full)) as archive:
        metadata = json.loads(archive.read("problem.json"))
        assert archive.read("validator/validator.py") == b"print('ready')\n"
        assert metadata["custom_validator"]["source_file"] == "validator/validator.py"
        assert metadata["custom_validator"]["language_id"] == "python3"
        assert archive.read("in/001.in") == b"example\n"
        # An interactive problem has no expected output to ship, even if a stale
        # legacy .out file is still present on disk.
        assert "out/001.out" not in archive.namelist()
    with zipfile.ZipFile(io.BytesIO(public)) as archive:
        assert "validator/validator.py" not in archive.namelist()
        assert "problem.json" not in archive.namelist()

    reimported = await import_problem_from_zip(
        session,
        contest,
        full,
        settings.PROBLEM_TESTCASE_DIR,
        settings.PROBLEM_STATEMENT_DIR,
        ImageProcessingService(),
    )
    reloaded = await get_problem_in_contest(session, contest, reimported.problem.id)
    assert reloaded is not None
    second = export_bytes(reloaded, await get_language_limits_map(session, reloaded))
    assert _fingerprint(second) == _fingerprint(full)


@pytest.mark.asyncio
async def test_validator_schema_rejects_pending_candidate_diagnostics(session: AsyncSession, uberadmin) -> None:
    await _make_language(session, "python3", "Python 3")
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.commit()
    imported = await import_problem_from_zip(
        session,
        contest,
        _validator_problem_zip(),
        settings.PROBLEM_TESTCASE_DIR,
        settings.PROBLEM_STATEMENT_DIR,
        ImageProcessingService(),
    )

    with pytest.raises(IntegrityError):
        await session.execute(
            update(ProblemCustomValidator)
            .where(ProblemCustomValidator.problem_id == imported.problem.id)
            .values(candidate_compile_log="not allowed while pending")
        )
    await session.rollback()


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
        ImageProcessingService(),
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
        ImageProcessingService(),
    )

    assert result.problem.color in BALLOON_COLORS


@pytest.mark.asyncio
async def test_sample_package_imports_cleanly_into_a_contest(session: AsyncSession, uberadmin) -> None:
    """The package offered for download on the import page must actually import."""
    python = await _make_language(session, "python3", "Python 3")
    rust = await _make_language(session, "rust", "Rust")
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.execute(
        insert(contest_languages_table),
        [
            {"contest_id": contest.id, "language_id": python.id},
            {"contest_id": contest.id, "language_id": rust.id},
        ],
    )
    await session.commit()

    result = await import_problem_from_zip(
        session,
        contest,
        _sample_package_bytes(),
        settings.PROBLEM_TESTCASE_DIR,
        settings.PROBLEM_STATEMENT_DIR,
        ImageProcessingService(),
    )

    problem = result.problem
    test_cases = sorted(
        await session.scalars(select(ProblemTestCase).where(ProblemTestCase.problem_id == problem.id)),
        key=lambda tc: tc.ordinal,
    )
    limits = {
        limit.language_id: limit
        for limit in await session.scalars(
            select(ProblemLanguageLimit).where(ProblemLanguageLimit.problem_id == problem.id)
        )
    }

    assert problem.title == "A + B"
    assert problem.author == "John Doe"
    assert problem.notes == "Sample problem"
    assert problem.time_limit_ms == 1000
    assert [tc.ordinal for tc in test_cases] == [1, 2, 3]
    assert test_cases[0].explanation is not None
    assert set(limits) == {"python3", "rust"}
    # A per-run limit, imported verbatim: the sample package is written at the
    # current format version, so nothing is divided on the way in.
    assert limits["python3"].time_limit_ms == 1000
    assert limits["python3"].repetitions == 3
    assert limits["rust"].memory_limit_kb == 131072
    assert {category.name for category in problem.categories} == {"sample", "math"}


def _sample_package_bytes() -> bytes:
    """Return the downloadable reference package as bytes."""
    destination = Path(tempfile.mkdtemp(prefix="noca-test-sample-")) / "sample.zip"
    return build_sample_problem_package(destination).read_bytes()


def _png_bytes(color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _problem_zip_with_image(
    *,
    image_name: str | None = "image.png",
    image_bytes: bytes | None = None,
    caption: str | None = "A red square",
) -> bytes:
    payload: dict[str, object] = {
        "title": "Illustrated Problem",
        "time_limit_ms": 1000,
        "memory_limit_kb": 262144,
        "pids_limit": 64,
        "image": image_name,
        "image_caption": caption,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("problem.json", json.dumps(payload))
        archive.writestr("statement.md", "# Illustrated\n\nNo external links here.\n")
        archive.writestr("in/001.in", "1 2\n")
        archive.writestr("out/001.out", "3\n")
        if image_bytes is not None:
            archive.writestr(image_name or "image.png", image_bytes)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_import_reads_packaged_image_and_caption(session: AsyncSession, uberadmin) -> None:
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.commit()

    result = await import_problem_from_zip(
        session,
        contest,
        _problem_zip_with_image(image_bytes=_png_bytes()),
        settings.PROBLEM_TESTCASE_DIR,
        settings.PROBLEM_STATEMENT_DIR,
        ImageProcessingService(),
    )

    assert result.problem.problem_image_base64
    assert result.problem.problem_image_mime == "image/png"
    assert result.problem.problem_image_caption == "A red square"


@pytest.mark.asyncio
async def test_import_rejects_missing_referenced_image(session: AsyncSession, uberadmin) -> None:
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.commit()

    with pytest.raises(ValueError, match="not present in the ZIP"):
        await import_problem_from_zip(
            session,
            contest,
            _problem_zip_with_image(image_bytes=None),
            settings.PROBLEM_TESTCASE_DIR,
            settings.PROBLEM_STATEMENT_DIR,
            ImageProcessingService(),
        )


@pytest.mark.asyncio
async def test_import_rejects_oversized_image(session: AsyncSession, uberadmin) -> None:
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.commit()

    noise = os.urandom(1600 * 1600 * 3)
    buffer = io.BytesIO()
    Image.frombytes("RGB", (1600, 1600), noise).save(buffer, format="PNG", compress_level=0)
    oversized = buffer.getvalue()
    assert len(oversized) > 2 * 1024 * 1024

    with pytest.raises(ValueError, match="Invalid problem image"):
        await import_problem_from_zip(
            session,
            contest,
            _problem_zip_with_image(image_bytes=oversized),
            settings.PROBLEM_TESTCASE_DIR,
            settings.PROBLEM_STATEMENT_DIR,
            ImageProcessingService(),
        )


@pytest.mark.asyncio
async def test_image_round_trips_through_export_and_public_zip_carries_it(
    session: AsyncSession,
    uberadmin,
) -> None:
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.commit()

    imported = await import_problem_from_zip(
        session,
        contest,
        _problem_zip_with_image(image_bytes=_png_bytes()),
        settings.PROBLEM_TESTCASE_DIR,
        settings.PROBLEM_STATEMENT_DIR,
        ImageProcessingService(),
    )
    problem = await get_problem_in_contest(session, contest, imported.problem.id)
    assert problem is not None

    full = export_bytes(problem, await get_language_limits_map(session, problem))
    public = export_bytes(problem, profile="public")

    with zipfile.ZipFile(io.BytesIO(full)) as archive:
        metadata = json.loads(archive.read("problem.json"))
        assert metadata["image"] == "image.png"
        assert metadata["image_caption"] == "A red square"
        assert archive.read("image.png")

    # The image is part of the statement a contestant reads, so the public ZIP
    # carries it even though it has no problem.json.
    with zipfile.ZipFile(io.BytesIO(public)) as archive:
        assert "problem.json" not in archive.namelist()
        assert archive.read("image.png")

    reimported = await import_problem_from_zip(
        session,
        contest,
        full,
        settings.PROBLEM_TESTCASE_DIR,
        settings.PROBLEM_STATEMENT_DIR,
        ImageProcessingService(),
    )
    assert reimported.problem.problem_image_base64 == problem.problem_image_base64
    assert reimported.problem.problem_image_mime == "image/png"
    assert reimported.problem.problem_image_caption == "A red square"


@pytest.mark.asyncio
async def test_same_domain_round_trip_is_lossless(session: AsyncSession, uberadmin) -> None:
    """export → import → export must produce the same parsed package."""
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.commit()

    imported = await import_problem_from_zip(
        session,
        contest,
        _problem_zip_with_explanation(),
        settings.PROBLEM_TESTCASE_DIR,
        settings.PROBLEM_STATEMENT_DIR,
        ImageProcessingService(),
    )
    problem = await get_problem_in_contest(session, contest, imported.problem.id)
    assert problem is not None

    first = export_bytes(problem, await get_language_limits_map(session, problem))
    reimported = await import_problem_from_zip(
        session,
        contest,
        first,
        settings.PROBLEM_TESTCASE_DIR,
        settings.PROBLEM_STATEMENT_DIR,
        ImageProcessingService(),
    )
    reloaded = await get_problem_in_contest(session, contest, reimported.problem.id)
    assert reloaded is not None
    second = export_bytes(reloaded, await get_language_limits_map(session, reloaded))

    # Raw ZIP bytes carry timestamps, so compare the entire normalized package.
    assert _fingerprint(second) == _fingerprint(first)


@pytest.mark.asyncio
async def test_cross_domain_import_drops_exactly_the_documented_fields(session: AsyncSession, uberadmin) -> None:
    """An Arena-shaped package loses only what the compatibility matrix names."""
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.commit()

    arena_shaped = _arena_shaped_package()
    imported = await import_problem_from_zip(
        session,
        contest,
        arena_shaped,
        settings.PROBLEM_TESTCASE_DIR,
        settings.PROBLEM_STATEMENT_DIR,
        ImageProcessingService(),
    )
    problem = await get_problem_in_contest(session, contest, imported.problem.id)
    assert problem is not None

    before = _parse(arena_shaped).metadata
    after = _parse(export_bytes(problem, await get_language_limits_map(session, problem))).metadata

    preserved = ("title", "author", "notes", "time_limit_ms", "memory_limit_kb", "pids_limit")
    assert {field: getattr(after, field) for field in preserved} == {
        field: getattr(before, field) for field in preserved
    }
    # Exactly these, and nothing else, are lost: Contest has nowhere to store them.
    dropped = {
        field
        for field in ("source", "license", "statement_language", "hide_author_show_source", "expected_difficulty")
        if getattr(before, field) and not getattr(after, field)
    }
    assert dropped == {"source", "license", "statement_language", "hide_author_show_source", "expected_difficulty"}


def _parse(zip_bytes: bytes):
    """Parse package bytes into the frozen comparison unit."""
    path = Path(tempfile.mkdtemp(prefix="noca-test-parse-")) / "package.zip"
    path.write_bytes(zip_bytes)
    with read_problem_package(path) as staged:
        return staged.package


def _fingerprint(zip_bytes: bytes) -> dict[str, object]:
    """Reduce a parsed package to all metadata and normalized payloads."""
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


def _arena_shaped_package() -> bytes:
    payload = {
        "format_version": 1,
        "title": "Arena Problem",
        "author": "Someone",
        "notes": "kept",
        "source": "ICPC 2025",
        "license": "CC BY-SA 4.0",
        "statement_language": "en",
        "hide_author_show_source": True,
        "expected_difficulty": 70,
        "time_limit_ms": 1500,
        "memory_limit_kb": 262144,
        "pids_limit": 64,
        "output_limit_in_bytes": 65536,
        "sample_testcases": [1],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("problem.json", json.dumps(payload))
        archive.writestr("statement.md", "# Arena Problem\n\nNo external links here.\n")
        archive.writestr("in/001.in", "1 2\n")
        archive.writestr("out/001.out", "3\n")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_import_refuses_a_validator_whose_language_is_not_active(session: AsyncSession, uberadmin) -> None:
    """Language availability is a target question the shared reader cannot answer."""
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.commit()

    with pytest.raises(ValueError, match="is not active"):
        await import_problem_from_zip(
            session,
            contest,
            _validator_problem_zip(),
            settings.PROBLEM_TESTCASE_DIR,
            settings.PROBLEM_STATEMENT_DIR,
            ImageProcessingService(),
        )
