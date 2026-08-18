#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Cross-module package round trips for the version-2 strategy discriminator.

The point of storing the strategy in the package is that a problem keeps its
kind when it crosses the Contest/Arena boundary. These tests exercise the real
importers and exporters of *both* domains against one another, which is the only
place a disagreement between them would show up.

They live under ``tests/arena`` because the Arena suite is the one that already
reaches into ``web`` (see ``tests/arena/_admin_problem_app.py``).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings as arena_settings
from arena.models.arena_problems import ArenaProblemCustomValidator
from arena.services import admin_problem_service
from shared.enumerations import ProblemValidatorType
from shared.services.custom_validator import stage_candidate
from shared.services.imageprocessing_service import ImageProcessingService
from shared.services.problem_package import PackageError, read_problem_package
from tests.arena.test_admin_problem_io import (  # reuse the Arena io harness
    _build_validator_package,
    _export_zip,
    _import_zip,
    _make_author,
)
from tests.web.test_problem_import_service import (  # reuse the Contest io harness
    _make_contest,
    _make_language,
    export_bytes,
    import_problem_from_zip,
)
from web.config import settings as web_settings
from web.models.problem import ProblemCustomValidator
from web.services.problem_service import get_language_limits_map, get_problem_in_contest

_PYTHON = "python3"


def _metadata(zip_bytes: bytes):
    """Parse package bytes and return only their metadata."""
    path = Path(tempfile.mkdtemp(prefix="noca-test-xmod-")) / "package.zip"
    path.write_bytes(zip_bytes)
    with read_problem_package(path) as staged:
        return staged.package.metadata


async def _contest_export(session: AsyncSession, contest, problem_id: str) -> bytes:
    """Export a contest problem as full package bytes."""
    problem = await get_problem_in_contest(session, contest, problem_id)
    assert problem is not None
    return export_bytes(problem, await get_language_limits_map(session, problem))


async def _arena_export(session: AsyncSession, problem_id: str, author) -> bytes:
    """Reload an Arena problem with its relationships, then export it.

    ``export_problem_package`` requires ``categories``, ``test_cases``, and
    ``sample_interactions`` to be eagerly loaded, which a freshly committed
    instance is not.
    """
    problem = await admin_problem_service.get_problem(session, problem_id, caller_id=author.id, is_admin=True)
    assert problem is not None
    return _export_zip(problem, author.nome, arena_settings.PROBLEM_TESTCASE_DIR)


# ── Standard problems cross the boundary as standard ──────────────────────────


@pytest.mark.asyncio
async def test_a_standard_arena_problem_imports_into_contest_as_standard(session: AsyncSession, uberadmin) -> None:
    author = await _make_author(session)
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.commit()

    arena_side = await _import_zip(
        session,
        zip_bytes=_standard_package(),
        caller_id=author.id,
        image_service=ImageProcessingService(),
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )
    assert arena_side.problem.editorial == "# Editorial\n\nAdd the two values.\n"
    exported = await _arena_export(session, arena_side.problem.id, author)
    assert _metadata(exported).validator_type is ProblemValidatorType.STANDARD

    imported = await import_problem_from_zip(
        session,
        contest,
        exported,
        web_settings.PROBLEM_TESTCASE_DIR,
        web_settings.PROBLEM_STATEMENT_DIR,
        ImageProcessingService(),
    )

    assert imported.problem.validator_type is ProblemValidatorType.STANDARD
    assert imported.problem.editorial == "# Editorial\n\nAdd the two values.\n"


# ── Interactive problems keep their kind, and their inputs-only cases ─────────


@pytest.mark.asyncio
async def test_an_interactive_arena_problem_imports_into_contest_as_interactive(
    session: AsyncSession, uberadmin
) -> None:
    await _make_language(session, _PYTHON, "Python 3")
    author = await _make_author(session)
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.commit()

    result = await _import_zip(
        session,
        zip_bytes=_build_validator_package(),
        caller_id=author.id,
        image_service=ImageProcessingService(),
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )
    assert result.problem.validator_type is ProblemValidatorType.INTERACTIVE

    exported = await _arena_export(session, result.problem.id, author)
    assert _metadata(exported).validator_type is ProblemValidatorType.INTERACTIVE

    imported = await import_problem_from_zip(
        session,
        contest,
        exported,
        web_settings.PROBLEM_TESTCASE_DIR,
        web_settings.PROBLEM_STATEMENT_DIR,
        ImageProcessingService(),
    )

    assert imported.problem.validator_type is ProblemValidatorType.INTERACTIVE
    # An interactive problem's cases carry input only, in both domains.
    assert all(case.output_size_bytes is None for case in imported.problem.test_cases)


@pytest.mark.asyncio
async def test_an_interactive_contest_problem_imports_into_arena_as_interactive(
    session: AsyncSession, uberadmin
) -> None:
    await _make_language(session, _PYTHON, "Python 3")
    author = await _make_author(session)
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.commit()

    contest_side = await import_problem_from_zip(
        session,
        contest,
        _build_validator_package(),
        web_settings.PROBLEM_TESTCASE_DIR,
        web_settings.PROBLEM_STATEMENT_DIR,
        ImageProcessingService(),
    )
    assert contest_side.problem.validator_type is ProblemValidatorType.INTERACTIVE

    exported = await _contest_export(session, contest, contest_side.problem.id)
    assert _metadata(exported).validator_type is ProblemValidatorType.INTERACTIVE

    arena_side = await _import_zip(
        session,
        zip_bytes=exported,
        caller_id=author.id,
        image_service=ImageProcessingService(),
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )

    assert arena_side.problem.validator_type is ProblemValidatorType.INTERACTIVE
    assert arena_side.is_interactive is True


# ── A stale validator row never changes what a package says ───────────────────


@pytest.mark.asyncio
async def test_a_standard_arena_problem_with_a_stale_validator_exports_as_standard(
    session: AsyncSession,
) -> None:
    """The state phase 2 made reachable: standard problem, leftover validator row.

    Version 2 says ``standard`` and must therefore ship neither a
    ``custom_validator`` declaration nor any ``validator/`` member -- otherwise
    the export is a package this build's own reader refuses.
    """
    author = await _make_author(session)
    arena_side = await _import_zip(
        session,
        zip_bytes=_standard_package(),
        caller_id=author.id,
        image_service=ImageProcessingService(),
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )
    problem = arena_side.problem

    stale = ArenaProblemCustomValidator(problem_id=problem.id)
    stage_candidate(stale, language_id=_PYTHON, source="print('stale')\n")
    session.add(stale)
    await session.commit()

    exported = await _arena_export(session, problem.id, author)

    metadata = _metadata(exported)
    assert metadata.validator_type is ProblemValidatorType.STANDARD
    assert metadata.custom_validator is None


@pytest.mark.asyncio
async def test_a_standard_contest_problem_with_a_stale_validator_exports_as_standard(
    session: AsyncSession, uberadmin
) -> None:
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.commit()

    imported = await import_problem_from_zip(
        session,
        contest,
        _standard_package(),
        web_settings.PROBLEM_TESTCASE_DIR,
        web_settings.PROBLEM_STATEMENT_DIR,
        ImageProcessingService(),
    )
    stale = ProblemCustomValidator(problem_id=imported.problem.id)
    stage_candidate(stale, language_id=_PYTHON, source="print('stale')\n")
    session.add(stale)
    await session.commit()

    exported = await _contest_export(session, contest, imported.problem.id)

    metadata = _metadata(exported)
    assert metadata.validator_type is ProblemValidatorType.STANDARD
    assert metadata.custom_validator is None
    # And the round trip does not flip the strategy on the way back.
    reimported = await _import_zip(
        session,
        zip_bytes=exported,
        caller_id=(await _make_author(session)).id,
        image_service=ImageProcessingService(),
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )
    assert reimported.problem.validator_type is ProblemValidatorType.STANDARD


def _standard_package() -> bytes:
    """A minimal standard package, written at version 2."""
    import hashlib
    import io
    import json
    import zipfile

    buffer = io.BytesIO()
    editorial = "# Editorial\n\nAdd the two values.\n"
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "problem.json",
            json.dumps(
                {
                    "format_version": 2,
                    "validator_type": "standard",
                    "title": "Stale",
                    "editorial": {
                        "member": "editorial.md",
                        "sha256": hashlib.sha256(editorial.encode("utf-8")).hexdigest(),
                    },
                }
            ),
        )
        archive.writestr("statement.md", "# Stale\n\nNo external links here.\n")
        archive.writestr("editorial.md", editorial)
        archive.writestr("in/001.in", "1 2\n")
        archive.writestr("out/001.out", "3\n")
    return buffer.getvalue()


# ── Output checker is refused identically by both importers ───────────────────


def _checker_package() -> bytes:
    """A version-2 package declaring the reserved output-checker strategy."""
    import io
    import json
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "problem.json",
            json.dumps({"format_version": 2, "validator_type": "checker", "title": "Checker"}),
        )
        archive.writestr("statement.md", "# Checker\n\nNo external links here.\n")
        archive.writestr("in/001.in", "1 2\n")
        archive.writestr("out/001.out", "3\n")
    return buffer.getvalue()


_UNSUPPORTED = "Output checker validation is not available in this build"


@pytest.mark.asyncio
async def test_both_importers_refuse_a_checker_package_identically(session: AsyncSession, uberadmin) -> None:
    """One rejection, raised in the shared parser, surfaced the same way by both.

    Neither importer implements the check, which is precisely what stops the two
    domains from diverging on it -- so both must fail with the same message, and
    neither may persist a disabled draft.
    """
    author = await _make_author(session)
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.commit()

    with pytest.raises(PackageError, match=_UNSUPPORTED):
        await import_problem_from_zip(
            session,
            contest,
            _checker_package(),
            web_settings.PROBLEM_TESTCASE_DIR,
            web_settings.PROBLEM_STATEMENT_DIR,
            ImageProcessingService(),
        )

    with pytest.raises(PackageError, match=_UNSUPPORTED):
        await _import_zip(
            session,
            zip_bytes=_checker_package(),
            caller_id=author.id,
            image_service=ImageProcessingService(),
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )

    # Nothing was persisted on either side.
    assert (await session.execute(select(ArenaProblemCustomValidator))).scalars().first() is None
