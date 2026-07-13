from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import contest_languages as contest_languages_table
from shared.enumerations import CustomValidatorCandidateState
from shared.services.imageprocessing_service import ImageProcessingService
from shared.services.sample_problem_package import build_sample_problem_package
from web.config import settings
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import ProblemCustomValidator, ProblemLanguageLimit, ProblemTestCase
from web.services.problem_service import (
    BALLOON_COLORS,
    build_export_zip,
    build_public_export_zip,
    get_language_limits_map,
    get_problem_in_contest,
    import_problem_from_zip,
)
from web.services.problem_service.importing import _pick_balloon_color


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
                "output_limit_in_bytes": "",
                "repetitions": 2,
            },
            "java": {
                "time_limit_ms": "2000",
                "memory_limit_kb": "262144",
                "pids_limit": "64",
                "output_limit_in_bytes": "",
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

    assert result.skipped_language_ids == [java.id]
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


def _validator_problem_zip(*, visibility: str = "sample") -> bytes:
    payload = {
        "title": "Interactive Problem",
        "time_limit_ms": 1000,
        "memory_limit_kb": 262144,
        "pids_limit": 64,
        "test_case_visibility": visibility,
        "custom_validator": {
            "language_id": "python3",
            "source_file": "validator/source.txt",
        },
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("problem.json", json.dumps(payload))
        archive.writestr("statement.md", "# Interactive\n\nTalk to the validator.\n")
        archive.writestr("validator/source.txt", "print('ready')\n")
        archive.writestr("in/001.in", "example\n")
        archive.writestr("out/001.out", "example\n")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_validator_package_import_stages_candidate_and_samples(
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
    assert test_cases and all(test_case.is_sample for test_case in test_cases)


@pytest.mark.asyncio
async def test_validator_package_requires_sample_visibility(session: AsyncSession, uberadmin) -> None:
    await _make_language(session, "python3", "Python 3")
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.commit()

    with pytest.raises(ValueError, match="test_case_visibility"):
        await import_problem_from_zip(
            session,
            contest,
            _validator_problem_zip(visibility="secret"),
            settings.PROBLEM_TESTCASE_DIR,
            settings.PROBLEM_STATEMENT_DIR,
            ImageProcessingService(),
        )


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

    full = build_export_zip(
        problem,
        settings.PROBLEM_TESTCASE_DIR,
        settings.PROBLEM_STATEMENT_DIR,
        await get_language_limits_map(session, problem),
    )
    public = build_public_export_zip(
        problem,
        settings.PROBLEM_TESTCASE_DIR,
        settings.PROBLEM_STATEMENT_DIR,
    )

    with zipfile.ZipFile(io.BytesIO(full)) as archive:
        metadata = json.loads(archive.read("problem.json"))
        assert archive.read("validator/source.txt") == b"print('ready')\n"
        assert metadata["test_case_visibility"] == "sample"
        assert metadata["custom_validator"]["language_id"] == "python3"
    with zipfile.ZipFile(io.BytesIO(public)) as archive:
        assert "validator/source.txt" not in archive.namelist()
        assert "problem.json" not in archive.namelist()


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
        build_sample_problem_package(),
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
    assert result.skipped_language_ids == []
    assert set(limits) == {"python3", "rust"}
    assert limits["python3"].time_limit_ms == 3000
    assert limits["python3"].repetitions == 3
    assert limits["rust"].memory_limit_kb == 131072
    assert {category.name for category in problem.categories} == {"sample", "math"}


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

    full = build_export_zip(
        problem,
        settings.PROBLEM_TESTCASE_DIR,
        settings.PROBLEM_STATEMENT_DIR,
        await get_language_limits_map(session, problem),
    )
    public = build_public_export_zip(
        problem,
        settings.PROBLEM_TESTCASE_DIR,
        settings.PROBLEM_STATEMENT_DIR,
    )

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
