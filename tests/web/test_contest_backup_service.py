#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Round-trip tests for the full contest backup/restore service."""

from __future__ import annotations

import json
import os
import zipfile
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import clarifications as clarifications_t
from shared.db_schema import contest_languages as contest_languages_t
from shared.db_schema import contests as contests_t
from shared.db_schema import human_submission_confirmations as confirmations_t
from shared.db_schema import languages as languages_t
from shared.db_schema import problem_custom_validators as validators_t
from shared.db_schema import submission_interactive_attempts as interactive_attempts_t
from shared.db_schema import submission_judgment_audit as judgment_audit_t
from shared.db_schema import submission_judgments as judgments_t
from shared.db_schema import submission_test_results as test_results_t
from shared.db_schema import submissions as submissions_t
from shared.db_schema import tasks as tasks_t
from shared.db_schema import test_cases as test_cases_t
from shared.db_schema import users as users_t
from shared.db_schema import users_media as users_media_t
from shared.db_schema import verdict_overrides as verdict_overrides_t
from shared.enumerations import (
    CustomValidatorActiveState,
    JudgmentStatus,
    ProblemValidatorType,
    RoleEnum,
    TaskType,
    Verdict,
)
from web.config import settings
from web.models.contest import Contest
from web.models.problem import Problem, ProblemTestCase
from web.models.site import Site
from web.models.users import UberAdmin, User
from web.services.contest_backup_service import (
    FORMAT_VERSION,
    ContestBackupError,
    build_contest_backup,
    import_contest_backup,
)
from web.services.contest_backup_service.export import _append_problem_folder
from web.services.contest_backup_service.models import LEGACY_FORMAT_VERSION, PREVIOUS_FORMAT_VERSION
from web.services.problem_service.files import save_md_statement, save_testcase_files

LANGUAGE_ID = "python3"


def test_problem_package_members_are_streamed_into_the_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_path = tmp_path / "problem.zip"
    backup_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(package_path, "w") as package:
        package.writestr("in/001.in", b"x" * (2 * 1024 * 1024))
    with zipfile.ZipFile(backup_path, "w"):
        pass

    read_sizes: list[int] = []
    original_read = zipfile.ZipExtFile.read

    def tracked_read(handle: zipfile.ZipExtFile, size: int = -1) -> bytes:
        read_sizes.append(size)
        return original_read(handle, size)

    monkeypatch.setattr(zipfile.ZipExtFile, "read", tracked_read)

    _append_problem_folder(backup_path, "problems/p1", package_path)

    assert read_sizes
    assert all(size == 1024 * 1024 for size in read_sizes)
    assert not package_path.exists()
    with zipfile.ZipFile(backup_path) as backup:
        assert backup.getinfo("problems/p1/in/001.in").file_size == 2 * 1024 * 1024


async def _seed_language(session: AsyncSession) -> None:
    await session.execute(
        insert(languages_t).values(
            id=LANGUAGE_ID,
            name="Python 3",
            icon="python",
            compile_image="img:compile",
            run_image="img:run",
            run_cmd=["python3", "/src.py"],
            source_filename="src.py",
            artifact_path="/src.py",
        )
    )


async def _seed_contest(session: AsyncSession, uberadmin: UberAdmin) -> Contest:
    """Seed a finished contest with all five roles, sites, problem, and a run."""
    await _seed_language(session)
    start = datetime.now(UTC) - timedelta(hours=5)
    contest = Contest(
        contest_name="Backup Source",
        contest_url="http://src.example.com",
        login_slug="backup-source",
        start_time=start,
        duration_minutes=60,
        stop_answers_after=60,
        stop_updating_scoreboard=60,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    session.add(contest)
    await session.flush()
    await session.execute(insert(contest_languages_t).values(contest_id=contest.id, language_id=LANGUAGE_ID))

    site_a = Site(sitename="Alpha", sitename_normalized="alpha", contest_id=contest.id)
    site_b = Site(sitename="Beta", sitename_normalized="beta", contest_id=contest.id)
    session.add_all([site_a, site_b])
    await session.flush()

    users: dict[RoleEnum, User] = {}
    for role in (RoleEnum.ADMIN, RoleEnum.JUDGE, RoleEnum.STAFF, RoleEnum.TEAM, RoleEnum.USER):
        user = User(
            username=f"{role.value.lower()}_u",
            fullname=f"{role.value} User",
            role=role,
            contest_id=contest.id,
            created_by_uberadmin_id=uberadmin.id,
            site_id=site_a.id if role in (RoleEnum.TEAM, RoleEnum.STAFF) else None,
        )
        user.password = "TestPass1!"
        session.add(user)
        users[role] = user
    await session.flush()

    contest.owner_user_id = users[RoleEnum.ADMIN].id
    contest.chief_judge_id = users[RoleEnum.JUDGE].id

    # A photo/audio media row so the include_media flag has something to carry.
    await session.execute(
        insert(users_media_t).values(
            user_id=users[RoleEnum.TEAM].id,
            com_foto=True,
            foto_base64="Zm90bw==",
            avatar_base64="YXZhdGFy",
            foto_mime="image/png",
        )
    )

    problem = Problem(
        contest_id=contest.id,
        title="Backup Problem",
        ordinal=1,
        color="#123abc",
        # The seed stages an active validator and input-only cases below, so the
        # problem really is interactive; its stored strategy must say so.
        validator_type=ProblemValidatorType.INTERACTIVE,
    )
    session.add(problem)
    await session.flush()
    sample = ProblemTestCase(problem_id=problem.id, ordinal=1, is_sample=True, input_size_bytes=2, output_size_bytes=2)
    secret = ProblemTestCase(problem_id=problem.id, ordinal=2, is_sample=False, input_size_bytes=2, output_size_bytes=2)
    session.add_all([sample, secret])
    await session.flush()

    save_md_statement(problem.id, "# Backup Problem\n", settings.PROBLEM_STATEMENT_DIR)
    for test_case in (sample, secret):
        save_testcase_files(problem.id, test_case.ordinal, b"1\n", None, settings.PROBLEM_TESTCASE_DIR)
        test_case.output_size_bytes = None

    now = datetime.now(UTC)
    await session.execute(
        insert(validators_t).values(
            problem_id=problem.id,
            active_language_id=LANGUAGE_ID,
            active_source="print('validator')",
            active_state=CustomValidatorActiveState.VALID,
            active_validated_at=now,
        )
    )

    await _seed_run(session, contest, problem, users, sample_id=sample.id)
    await session.flush()
    return contest


async def _seed_run(
    session: AsyncSession,
    contest: Contest,
    problem: Problem,
    users: dict[RoleEnum, User],
    *,
    sample_id: str,
) -> None:
    """Seed a submission with a SUPERSEDED and a DONE judgment plus children."""
    team = users[RoleEnum.TEAM]
    now = datetime.now(UTC)
    submission_id = "sub-1"
    await session.execute(
        insert(submissions_t).values(
            id=submission_id,
            problem_id=problem.id,
            team_id=team.id,
            language_id=LANGUAGE_ID,
            source_code="print(1)",
            source_hash="hash1",
            source_size_bytes=8,
            timestamp_seconds=42,
            created_at=now,
        )
    )
    old_judgment = "judg-old"
    new_judgment = "judg-new"
    await session.execute(
        insert(judgments_t).values(
            id=old_judgment,
            submission_id=submission_id,
            status=JudgmentStatus.SUPERSEDED,
            autojudge_verdict=Verdict.WA,
            final_verdict=None,
            created_at=now,
            timestamp_seconds=42,
        )
    )
    await session.execute(
        insert(judgments_t).values(
            id=new_judgment,
            submission_id=submission_id,
            status=JudgmentStatus.DONE,
            autojudge_verdict=Verdict.AC,
            final_verdict=Verdict.AC,
            created_at=now + timedelta(seconds=1),
            timestamp_seconds=43,
        )
    )
    await session.execute(
        insert(test_results_t).values(
            id="tr-1",
            judgment_id=new_judgment,
            test_case_id=sample_id,
            verdict=Verdict.AC,
            created_at=now,
        )
    )
    await session.execute(
        insert(confirmations_t).values(
            id="cf-1",
            judgment_id=new_judgment,
            judge_id=users[RoleEnum.JUDGE].id,
            confirmed_verdict=Verdict.AC,
            is_chief_confirmation=True,
            created_at=now,
            timestamp_seconds=43,
        )
    )
    await session.execute(
        insert(verdict_overrides_t).values(
            id="ov-1",
            submission_id=submission_id,
            judgment_id=new_judgment,
            overridden_by=users[RoleEnum.ADMIN].id,
            original_verdict=Verdict.WA,
            new_verdict=Verdict.AC,
            reason="chief override",
            created_at=now,
            updated_at=now,
            timestamp_seconds=43,
        )
    )
    await session.execute(
        insert(judgment_audit_t).values(
            id="au-1",
            judgment_id=new_judgment,
            submission_id=submission_id,
            actor_user_id=users[RoleEnum.JUDGE].id,
            event_source="model_hook",
            event_type="created",
            to_status=JudgmentStatus.DONE,
            to_verdict=Verdict.AC,
            created_at=now,
            timestamp_seconds=43,
        )
    )
    await session.execute(
        insert(interactive_attempts_t).values(
            id="ia-1",
            judgment_id=new_judgment,
            attempt_number=1,
            test_case_ordinal=1,
            contestant_exit_code=0,
            validator_exit_code=0,
            transcript={"lines": [{"dir": "user", "line": "1"}], "truncated": False},
            validator_verdict=Verdict.AC,
            created_at=now,
        )
    )
    await session.execute(
        insert(clarifications_t).values(
            id="cl-1",
            team_id=team.id,
            judge_id=users[RoleEnum.JUDGE].id,
            problem_id=problem.id,
            question="Why?",
            answer="Because.",
            created_timestamp_seconds=10,
            created_at=now,
            updated_at=now,
        )
    )
    await session.execute(
        insert(clarifications_t).values(
            id="cl-2",
            team_id=team.id,
            problem_id=None,
            question="Where is the printer?",
            created_timestamp_seconds=15,
            created_at=now,
            updated_at=now,
        )
    )
    await session.execute(
        insert(tasks_t).values(
            id="tk-1",
            team_id=team.id,
            staff_id=users[RoleEnum.STAFF].id,
            type=TaskType.BALLOON,
            problem_id=problem.id,
            created_timestamp_seconds=20,
            source_code="",
            source_hash="th",
            source_size_bytes=0,
            created_at=now,
            updated_at=now,
        )
    )


async def _export(
    session: AsyncSession, contest: Contest, tmp_path: Path, *, hashes: bool = False, media: bool = False
) -> Path:
    dest = tmp_path / "backup.zip"
    await build_contest_backup(session, contest, dest, include_password_hashes=hashes, include_media=media)
    return dest


async def _restore(
    session: AsyncSession, zip_path: Path, uberadmin: UberAdmin, *, slug: str = "restored", name: str = "Restored"
) -> Contest:
    result = await import_contest_backup(
        session,
        zip_path,
        actor_uberadmin=uberadmin,
        new_name=name,
        new_slug=slug,
        testcase_dir=settings.PROBLEM_TESTCASE_DIR,
        statement_dir=settings.PROBLEM_STATEMENT_DIR,
    )
    return (await session.execute(select(Contest).where(Contest.id == result.contest_id))).scalar_one()


@pytest.mark.asyncio
async def test_round_trip_restores_all_entities(session: AsyncSession, uberadmin: UberAdmin, tmp_path: Path) -> None:
    contest = await _seed_contest(session, uberadmin)
    await session.commit()

    zip_path = await _export(session, contest, tmp_path)
    restored = await _restore(session, zip_path, uberadmin)

    assert restored.login_slug == "restored"
    assert restored.contest_name == "Restored"
    users = (
        await session.execute(select(func.count()).select_from(users_t).where(users_t.c.contest_id == restored.id))
    ).scalar_one()
    assert users == 5
    result = await session.execute(select(Problem).where(Problem.contest_id == restored.id))
    problems = result.scalars().all()
    assert len(problems) == 1
    assert problems[0].color == "#123abc"
    assert restored.owner_user_id is not None and restored.chief_judge_id is not None
    assert restored.created_by_uberadmin_id == uberadmin.id
    validator = (await session.execute(select(validators_t).where(validators_t.c.problem_id == problems[0].id))).one()
    assert validator.active_source == "print('validator')"

    restored_users = (
        (await session.execute(select(users_t.c.id).where(users_t.c.contest_id == restored.id))).scalars().all()
    )
    clarification_problems = (
        (
            await session.execute(
                select(clarifications_t.c.problem_id).where(clarifications_t.c.team_id.in_(restored_users))
            )
        )
        .scalars()
        .all()
    )
    # The general clarification keeps its NULL problem; the other one is remapped.
    assert sorted(clarification_problems, key=lambda value: value or "") == [None, problems[0].id]


@pytest.mark.asyncio
async def test_round_trip_preserves_verdicts_and_timestamps(
    session: AsyncSession, uberadmin: UberAdmin, tmp_path: Path
) -> None:
    contest = await _seed_contest(session, uberadmin)
    await session.commit()
    zip_path = await _export(session, contest, tmp_path)
    restored = await _restore(session, zip_path, uberadmin)

    problem = (await session.execute(select(Problem).where(Problem.contest_id == restored.id))).scalar_one()
    submissions = (await session.execute(select(submissions_t).where(submissions_t.c.problem_id == problem.id))).all()
    assert len(submissions) == 1
    assert submissions[0].timestamp_seconds == 42

    judgment_rows = (
        await session.execute(
            select(judgments_t)
            .join(submissions_t, judgments_t.c.submission_id == submissions_t.c.id)
            .where(submissions_t.c.problem_id == problem.id)
        )
    ).all()
    statuses = {row.status for row in judgment_rows}
    assert JudgmentStatus.SUPERSEDED in statuses and JudgmentStatus.DONE in statuses
    done = [row for row in judgment_rows if row.status == JudgmentStatus.DONE][0]
    assert done.final_verdict == Verdict.AC

    # Full judgment children survived and remapped (scoped to the restored contest).
    restored_submission_id = submissions[0].id
    tc_count = (
        await session.execute(
            select(func.count())
            .select_from(test_results_t)
            .join(judgments_t, test_results_t.c.judgment_id == judgments_t.c.id)
            .where(judgments_t.c.submission_id == restored_submission_id)
        )
    ).scalar_one()
    override_count = (
        await session.execute(
            select(func.count())
            .select_from(verdict_overrides_t)
            .where(verdict_overrides_t.c.submission_id == restored_submission_id)
        )
    ).scalar_one()
    assert tc_count == 1 and override_count == 1
    attempt_count = (
        await session.execute(
            select(func.count())
            .select_from(interactive_attempts_t)
            .join(judgments_t, interactive_attempts_t.c.judgment_id == judgments_t.c.id)
            .where(judgments_t.c.submission_id == restored_submission_id)
        )
    ).scalar_one()
    assert attempt_count == 1
    is_sample = {
        row.ordinal: row.is_sample
        for row in (await session.execute(select(test_cases_t).where(test_cases_t.c.problem_id == problem.id))).all()
    }
    assert is_sample == {1: True, 2: False}


@pytest.mark.asyncio
async def test_password_hashes_excluded_by_default(session: AsyncSession, uberadmin: UberAdmin, tmp_path: Path) -> None:
    contest = await _seed_contest(session, uberadmin)
    original = (
        (await session.execute(select(users_t.c.password_hash).where(users_t.c.contest_id == contest.id)))
        .scalars()
        .all()
    )
    await session.commit()

    zip_path = await _export(session, contest, tmp_path, hashes=False, media=False)
    with zipfile.ZipFile(zip_path) as archive:
        users_json = json.loads(archive.read("users.json"))
        assert "media.json" not in archive.namelist()
    assert all("password_hash" not in user for user in users_json)

    restored = await _restore(session, zip_path, uberadmin)
    restored_hashes = (
        (await session.execute(select(users_t.c.password_hash).where(users_t.c.contest_id == restored.id)))
        .scalars()
        .all()
    )
    assert all(h not in original for h in restored_hashes)
    assert all(h for h in restored_hashes)  # never NULL/blank


@pytest.mark.asyncio
async def test_sensitive_payloads_included_when_chosen(
    session: AsyncSession, uberadmin: UberAdmin, tmp_path: Path
) -> None:
    contest = await _seed_contest(session, uberadmin)
    team_hash = (
        await session.execute(
            select(users_t.c.password_hash).where(users_t.c.contest_id == contest.id, users_t.c.role == RoleEnum.TEAM)
        )
    ).scalar_one()
    await session.commit()

    zip_path = await _export(session, contest, tmp_path, hashes=True, media=True)
    restored = await _restore(session, zip_path, uberadmin)

    restored_team_hash = (
        await session.execute(
            select(users_t.c.password_hash).where(users_t.c.contest_id == restored.id, users_t.c.role == RoleEnum.TEAM)
        )
    ).scalar_one()
    assert restored_team_hash == team_hash
    media_count = (await session.execute(select(func.count()).select_from(users_media_t))).scalar_one()
    assert media_count == 2  # original + restored


@pytest.mark.asyncio
async def test_fail_closed_on_missing_language(session: AsyncSession, uberadmin: UberAdmin, tmp_path: Path) -> None:
    contest = await _seed_contest(session, uberadmin)
    await session.commit()
    zip_path = await _export(session, contest, tmp_path)

    tampered = tmp_path / "tampered.zip"
    _rewrite_member(zip_path, tampered, "submissions.json", _swap_language)

    with pytest.raises(ContestBackupError, match="not registered"):
        await _restore(session, tampered, uberadmin)
    count = (
        await session.execute(select(func.count()).select_from(contests_t).where(contests_t.c.login_slug == "restored"))
    ).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_slug_collision_rejected(session: AsyncSession, uberadmin: UberAdmin, tmp_path: Path) -> None:
    contest = await _seed_contest(session, uberadmin)
    await session.commit()
    zip_path = await _export(session, contest, tmp_path)
    with pytest.raises(ContestBackupError, match="already exists"):
        await _restore(session, zip_path, uberadmin, slug="backup-source")


@pytest.mark.asyncio
async def test_malicious_and_malformed_archives_rejected(
    session: AsyncSession, uberadmin: UberAdmin, tmp_path: Path
) -> None:
    contest = await _seed_contest(session, uberadmin)
    await session.commit()
    good = await _export(session, contest, tmp_path)

    # Path traversal member name.
    traversal = tmp_path / "traversal.zip"
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(traversal, "w") as dst:
        for info in src.infolist():
            dst.writestr(info.filename, src.read(info.filename))
        dst.writestr("../escape.txt", b"x")
    with pytest.raises(ContestBackupError, match="Unsafe"):
        await _restore(session, traversal, uberadmin, slug="t1")

    # Unsupported version.
    bad_version = tmp_path / "version.zip"
    _rewrite_member(good, bad_version, "manifest.json", lambda data: _bump_version(data))
    with pytest.raises(ContestBackupError, match="Unsupported"):
        await _restore(session, bad_version, uberadmin, slug="t2")

    # Not a zip at all.
    not_zip = tmp_path / "not.zip"
    not_zip.write_bytes(b"not a zip")
    with pytest.raises(ContestBackupError, match="valid ZIP"):
        await _restore(session, not_zip, uberadmin, slug="t3")

    # Duplicate member.
    duplicate = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(duplicate, "w") as dst:
        for info in src.infolist():
            dst.writestr(info.filename, src.read(info.filename))
        with pytest.warns(UserWarning, match="Duplicate name"):
            dst.writestr("users.json", src.read("users.json"))
    with pytest.raises(ContestBackupError, match="Duplicate"):
        await _restore(session, duplicate, uberadmin, slug="t4")

    # Absolute member name.
    absolute = tmp_path / "absolute.zip"
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(absolute, "w") as dst:
        for info in src.infolist():
            dst.writestr(info.filename, src.read(info.filename))
        dst.writestr("/escape.txt", b"x")
    with pytest.raises(ContestBackupError, match="Unsafe"):
        await _restore(session, absolute, uberadmin, slug="t5")


@pytest.mark.asyncio
async def test_missing_payload_and_dangling_ids_are_rejected_before_restore(
    session: AsyncSession, uberadmin: UberAdmin, tmp_path: Path
) -> None:
    contest = await _seed_contest(session, uberadmin)
    await session.commit()
    good = await _export(session, contest, tmp_path)

    missing_input = tmp_path / "missing-input.zip"
    _remove_member(good, missing_input, "problems/001/in/001.in")
    with pytest.raises(ContestBackupError, match="Missing required problem payload"):
        await _restore(session, missing_input, uberadmin, slug="missing-input")

    dangling = tmp_path / "dangling.zip"
    _rewrite_member(good, dangling, "judgments.json", _make_audit_actor_dangling)
    with pytest.raises(ContestBackupError, match="Dangling judgment audit actor"):
        await _restore(session, dangling, uberadmin, slug="dangling")

    malformed = tmp_path / "malformed.zip"
    _rewrite_member(good, malformed, "users.json", lambda _data: b"[null]")
    with pytest.raises(ContestBackupError, match="array of objects"):
        await _restore(session, malformed, uberadmin, slug="malformed")


@pytest.mark.asyncio
async def test_archive_and_upload_size_limits_are_enforced(
    session: AsyncSession,
    uberadmin: UberAdmin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contest = await _seed_contest(session, uberadmin)
    await session.commit()
    good = await _export(session, contest, tmp_path)

    import web.routes.uberadmin_contest_backup as route_module
    import web.services.contest_backup_service.validation as validation_module

    monkeypatch.setattr(validation_module, "MAX_MEMBER_BYTES", 1)
    with pytest.raises(ContestBackupError, match="per-file size limit"):
        await _restore(session, good, uberadmin, slug="oversized")

    monkeypatch.setattr(route_module, "MAX_ARCHIVE_BYTES", 3)
    upload_path = tmp_path / "upload.zip"
    handle = os.open(upload_path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    upload = UploadFile(file=BytesIO(b"1234"), filename="backup.zip")
    with pytest.raises(ContestBackupError, match="compressed upload size limit"):
        await route_module._save_upload_limited(upload, handle)
    await upload.close()
    assert upload_path.stat().st_size == 0


@pytest.mark.asyncio
async def test_rollback_cleans_up_on_failure(
    session: AsyncSession, uberadmin: UberAdmin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contest = await _seed_contest(session, uberadmin)
    await session.commit()
    zip_path = await _export(session, contest, tmp_path)
    testcase_files_before = set(settings.PROBLEM_TESTCASE_DIR.rglob("*"))
    statement_files_before = set(settings.PROBLEM_STATEMENT_DIR.rglob("*"))

    import web.services.contest_backup_service.restore as restore_mod

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("induced failure")

    monkeypatch.setattr(restore_mod, "restore_submissions", _boom)

    with pytest.raises(RuntimeError, match="induced"):
        await _restore(session, zip_path, uberadmin, slug="rollback")

    await session.rollback()
    count = (
        await session.execute(select(func.count()).select_from(contests_t).where(contests_t.c.login_slug == "rollback"))
    ).scalar_one()
    assert count == 0
    assert set(settings.PROBLEM_TESTCASE_DIR.rglob("*")) == testcase_files_before
    assert set(settings.PROBLEM_STATEMENT_DIR.rglob("*")) == statement_files_before


# --------------------------------------------------------------------------- #
# Archive-rewriting helpers
# --------------------------------------------------------------------------- #


def _rewrite_member(src_path: Path, dst_path: Path, member: str, transform) -> None:
    with zipfile.ZipFile(src_path) as src:
        payloads = {info.filename: src.read(info.filename) for info in src.infolist()}
    payloads[member] = transform(payloads[member])
    with zipfile.ZipFile(dst_path, "w") as dst:
        for name, data in payloads.items():
            dst.writestr(name, data)


def _remove_member(src_path: Path, dst_path: Path, member: str) -> None:
    with zipfile.ZipFile(src_path) as src, zipfile.ZipFile(dst_path, "w") as dst:
        for info in src.infolist():
            if info.filename != member:
                dst.writestr(info.filename, src.read(info.filename))


def _swap_language(data: bytes) -> bytes:
    submissions = json.loads(data)
    for submission in submissions:
        submission["language_id"] = "no-such-language"
    return json.dumps(submissions).encode("utf-8")


def _bump_version(data: bytes) -> bytes:
    manifest = json.loads(data)
    manifest["format_version"] = 999
    return json.dumps(manifest).encode("utf-8")


def _make_audit_actor_dangling(data: bytes) -> bytes:
    judgments = json.loads(data)
    audited = next(entry for entry in judgments if entry["audit"])
    audited["audit"][0]["actor_user_id"] = "missing-user"
    return json.dumps(judgments).encode("utf-8")


@pytest.mark.asyncio
async def test_solution_test_runs_are_excluded_from_the_archive(
    session: AsyncSession, uberadmin: UberAdmin, tmp_path: Path
) -> None:
    """Solution tests are operational data, excluded exactly as profiling runs are.

    The archive must carry no member for them, and a restore must therefore start
    with an empty solution-test history.
    """
    from shared.db_schema import solution_test_runs as solution_test_runs_t

    contest = await _seed_contest(session, uberadmin)
    problem = (await session.execute(select(Problem).where(Problem.contest_id == contest.id))).scalars().first()
    assert problem is not None
    language_id = (await session.execute(select(languages_t.c.id))).scalars().first()
    await session.execute(
        insert(solution_test_runs_t).values(
            id="dddddddd-dddd-dddd-dddd-dddddddddddd",
            problem_id=problem.id,
            language_id=language_id,
            source_code="print('staff')",
            source_hash="e" * 64,
            source_size_bytes=14,
            status=JudgmentStatus.DONE,
            verdict=Verdict.AC,
            triggered_by_label="judge-1",
        )
    )
    await session.commit()

    zip_path = await _export(session, contest, tmp_path)

    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        payload = json.loads(archive.read("manifest.json"))
    assert not any("solution" in name for name in names)
    assert not any("solution" in key for key in payload)
    # Excluding solution tests did not move FORMAT_VERSION: the archive simply
    # never contained this data. Assert the constant rather than a literal, so
    # an unrelated version bump does not fail this test for the wrong reason.
    assert payload["format_version"] == FORMAT_VERSION

    restored = await _restore(session, zip_path, uberadmin)
    restored_problems = (
        (await session.execute(select(Problem.id).where(Problem.contest_id == restored.id))).scalars().all()
    )
    restored_runs = (
        (
            await session.execute(
                select(solution_test_runs_t.c.id).where(solution_test_runs_t.c.problem_id.in_(restored_problems))
            )
        )
        .scalars()
        .all()
    )
    assert restored_runs == []


@pytest.mark.asyncio
async def test_legacy_backup_with_a_null_output_limit_is_still_restorable(
    session: AsyncSession, uberadmin: UberAdmin, tmp_path: Path
) -> None:
    """``problems.output_limit_in_bytes`` used to be nullable; NULL meant "no limit".

    The column is now NOT NULL and row validation rejects NULL for a non-nullable
    column, so an older backup would otherwise be unrestorable. The value is
    normalized to the documented default instead.
    """
    contest = await _seed_contest(session, uberadmin)
    await session.commit()
    zip_path = await _export(session, contest, tmp_path)

    legacy_path = tmp_path / "legacy-backup.zip"
    with zipfile.ZipFile(zip_path) as source, zipfile.ZipFile(legacy_path, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename.endswith("problems.json"):
                payload = json.loads(data)
                for entry in payload if isinstance(payload, list) else payload.get("rows", []):
                    entry["problem"]["output_limit_in_bytes"] = None
                data = json.dumps(payload).encode("utf-8")
            target.writestr(info.filename, data)

    restored = await _restore(session, legacy_path, uberadmin, slug="legacy", name="Legacy")

    result = await session.execute(select(Problem).where(Problem.contest_id == restored.id))
    problems = result.scalars().all()
    assert [problem.output_limit_in_bytes for problem in problems] == [65536]


@pytest.mark.asyncio
async def test_legacy_backup_without_a_stored_strategy_is_still_restorable(
    session: AsyncSession, uberadmin: UberAdmin, tmp_path: Path
) -> None:
    """An archive captured before ``problems.validator_type`` existed still restores.

    Strict row validation compares each row against the *live* table, so adding a
    NOT NULL column would otherwise break every existing archive on the day it
    lands. The v1 branch treats the new columns as optional and fills the strategy
    by the same inference the archive was captured under -- the last place that
    inference is still correct, because it is all such an archive carries.
    """
    contest = await _seed_contest(session, uberadmin)
    await session.commit()
    zip_path = await _export(session, contest, tmp_path)

    legacy_path = _downgrade_to_v1(zip_path, tmp_path / "legacy-backup.zip", strip_strategy=True)

    restored = await _restore(session, legacy_path, uberadmin, slug="legacy", name="Legacy")

    result = await session.execute(select(Problem).where(Problem.contest_id == restored.id))
    problems = result.scalars().all()
    # The seeded problem carries an active validator, which is the only signal a
    # pre-strategy archive holds; the fence starts from its server default.
    assert [problem.validator_type for problem in problems] == [ProblemValidatorType.INTERACTIVE]
    assert [problem.artifact_generation for problem in problems] == [0]


def _downgrade_to_v1(source_path: Path, destination: Path, *, strip_strategy: bool) -> Path:
    """Rewrite a current archive as a faithful version-1 one.

    Every versioned component is downgraded, not just the one under test: the
    manifest, the payload rows, and each embedded ``problem.json``. Leaving any
    of them at version 2 would produce an archive no release ever wrote, so the
    test would pass or fail for a reason unrelated to v1 compatibility.

    Args:
        source_path: The current-format archive to downgrade.
        destination: Where to write the downgraded archive.
        strip_strategy: Whether to also remove the columns version 1 predates,
            producing a *pre*-strategy archive rather than an interim one.

    Returns:
        Path: ``destination``.
    """
    with zipfile.ZipFile(source_path) as source, zipfile.ZipFile(destination, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "manifest.json":
                manifest = json.loads(data)
                manifest["format_version"] = LEGACY_FORMAT_VERSION
                data = json.dumps(manifest).encode("utf-8")
            elif info.filename.endswith("problems.json"):
                payload = json.loads(data)
                for entry in payload:
                    if strip_strategy:
                        entry["problem"].pop("validator_type", None)
                        entry["problem"].pop("artifact_generation", None)
                    entry["problem"].pop("editorial", None)
                data = json.dumps(payload).encode("utf-8")
            elif info.filename.endswith("problem.json"):
                embedded = json.loads(data)
                embedded["format_version"] = 1
                embedded.pop("validator_type", None)
                embedded.pop("editorial", None)
                data = json.dumps(embedded).encode("utf-8")
            elif info.filename.endswith("editorial.md"):
                continue
            target.writestr(info.filename, data)
    return destination


@pytest.mark.asyncio
async def test_an_interim_v1_backup_keeps_its_explicit_strategy(
    session: AsyncSession, uberadmin: UberAdmin, tmp_path: Path
) -> None:
    """A v1-labelled archive that *does* carry the strategy is not re-inferred.

    Between the strategy column landing and this format bump, the exporter wrote
    ``validator_type`` into archives still labelled version 1. Treating "version
    1" as "infer" would corrupt exactly those archives, so the rule is *explicit
    wins, infer only on absence*. The standard problem here carries a validator
    row, which is precisely what inference would get wrong.
    """
    contest = await _seed_contest(session, uberadmin)
    standard = Problem(
        contest_id=contest.id,
        title="Standard with a stale validator",
        ordinal=2,
        color="#00ff00",
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(standard)
    await session.flush()
    save_md_statement(standard.id, "# Standard\n", settings.PROBLEM_STATEMENT_DIR)
    await session.execute(
        insert(validators_t).values(
            problem_id=standard.id,
            active_language_id=LANGUAGE_ID,
            active_source="print('stale')",
            active_state=CustomValidatorActiveState.VALID,
            active_validated_at=datetime.now(UTC),
        )
    )
    await session.commit()
    zip_path = await _export(session, contest, tmp_path)

    interim_path = _downgrade_to_v1(zip_path, tmp_path / "interim-backup.zip", strip_strategy=False)

    restored = await _restore(session, interim_path, uberadmin, slug="interim", name="Interim")

    ordered = select(Problem).where(Problem.contest_id == restored.id).order_by(Problem.ordinal)
    restored_problems = (await session.execute(ordered)).scalars().all()
    assert [p.validator_type for p in restored_problems] == [
        ProblemValidatorType.INTERACTIVE,
        # Inference would have called this interactive; the stored value wins.
        ProblemValidatorType.STANDARD,
    ]


@pytest.mark.asyncio
async def test_a_v2_backup_omitting_the_strategy_is_refused(
    session: AsyncSession, uberadmin: UberAdmin, tmp_path: Path
) -> None:
    """The legacy tolerance is scoped to version 1, not applied unconditionally."""
    contest = await _seed_contest(session, uberadmin)
    await session.commit()
    zip_path = await _export(session, contest, tmp_path)

    broken_path = tmp_path / "broken-v2.zip"
    with zipfile.ZipFile(zip_path) as source, zipfile.ZipFile(broken_path, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename.endswith("problems.json"):
                payload = json.loads(data)
                for entry in payload:
                    entry["problem"].pop("validator_type", None)
                data = json.dumps(payload).encode("utf-8")
            target.writestr(info.filename, data)

    with pytest.raises(ContestBackupError, match="missing columns: validator_type"):
        await _restore(session, broken_path, uberadmin, slug="broken", name="Broken")


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [0, 4, 99])
async def test_an_unknown_backup_version_is_refused(
    session: AsyncSession, uberadmin: UberAdmin, tmp_path: Path, version: int
) -> None:
    contest = await _seed_contest(session, uberadmin)
    await session.commit()
    zip_path = await _export(session, contest, tmp_path)

    bad_path = tmp_path / f"version-{version}.zip"
    with zipfile.ZipFile(zip_path) as source, zipfile.ZipFile(bad_path, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "manifest.json":
                manifest = json.loads(data)
                manifest["format_version"] = version
                data = json.dumps(manifest).encode("utf-8")
            target.writestr(info.filename, data)

    with pytest.raises(ContestBackupError, match="Unsupported backup format version"):
        await _restore(session, bad_path, uberadmin, slug="bad", name="Bad")


@pytest.mark.asyncio
async def test_version_two_backup_without_editorial_restores_null(
    session: AsyncSession, uberadmin: UberAdmin, tmp_path: Path
) -> None:
    """Version 2 predates the problem-row editorial column."""
    contest = await _seed_contest(session, uberadmin)
    await session.commit()
    zip_path = await _export(session, contest, tmp_path)
    previous_path = tmp_path / "version-two.zip"

    with zipfile.ZipFile(zip_path) as source, zipfile.ZipFile(previous_path, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "manifest.json":
                manifest = json.loads(data)
                manifest["format_version"] = PREVIOUS_FORMAT_VERSION
                data = json.dumps(manifest).encode("utf-8")
            elif info.filename.endswith("problems.json"):
                payload = json.loads(data)
                for entry in payload:
                    entry["problem"].pop("editorial", None)
                data = json.dumps(payload).encode("utf-8")
            target.writestr(info.filename, data)

    restored = await _restore(session, previous_path, uberadmin, slug="version-two", name="Version Two")
    result = await session.execute(select(Problem).where(Problem.contest_id == restored.id))
    assert [problem.editorial for problem in result.scalars()] == [None]


@pytest.mark.asyncio
async def test_current_backup_round_trips_editorial(
    session: AsyncSession, uberadmin: UberAdmin, tmp_path: Path
) -> None:
    """Version 3 makes the nullable editorial column part of the strict row shape."""
    contest = await _seed_contest(session, uberadmin)
    problem = await session.scalar(select(Problem).where(Problem.contest_id == contest.id))
    assert problem is not None
    problem.editorial = "# Official solution\n\nUse a prefix sum."
    await session.commit()

    zip_path = await _export(session, contest, tmp_path)
    restored = await _restore(session, zip_path, uberadmin, slug="editorial", name="Editorial")

    restored_problem = await session.scalar(select(Problem).where(Problem.contest_id == restored.id))
    assert restored_problem is not None
    assert restored_problem.editorial == "# Official solution\n\nUse a prefix sum."


@pytest.mark.asyncio
async def test_a_contest_with_a_sourceless_interactive_problem_still_backs_up(
    session: AsyncSession, uberadmin: UberAdmin, tmp_path: Path
) -> None:
    """Losing a validator source must not make a contest un-backupable.

    A full problem package cannot represent an interactive problem with no
    validator source, but the archive's embedded package is a convenience
    artifact -- restore reads the payload rows, never that ``problem.json`` --
    so the backup waives that completeness rule rather than refusing.
    """
    contest = await _seed_contest(session, uberadmin)
    sourceless = Problem(
        contest_id=contest.id,
        title="Interactive, source removed",
        ordinal=2,
        color="#0000ff",
        validator_type=ProblemValidatorType.INTERACTIVE,
    )
    session.add(sourceless)
    await session.flush()
    save_md_statement(sourceless.id, "# Interactive\n", settings.PROBLEM_STATEMENT_DIR)
    await session.commit()

    zip_path = await _export(session, contest, tmp_path)
    restored = await _restore(session, zip_path, uberadmin, slug="sourceless", name="Sourceless")

    ordered = select(Problem).where(Problem.contest_id == restored.id).order_by(Problem.ordinal)
    restored_problems = (await session.execute(ordered)).scalars().all()
    assert [p.validator_type for p in restored_problems] == [
        ProblemValidatorType.INTERACTIVE,
        ProblemValidatorType.INTERACTIVE,
    ]


@pytest.mark.asyncio
async def test_a_current_backup_round_trips_the_stored_strategy(
    session: AsyncSession, uberadmin: UberAdmin, tmp_path: Path
) -> None:
    """The strategy survives export and restore rather than being re-inferred.

    This is why the export keeps the column instead of stripping it: a problem
    whose stored strategy disagrees with its validator rows -- here, an
    interactive problem that has no validator source at all -- is exactly the case
    inference gets wrong, and it must survive the round trip intact.
    """
    contest = await _seed_contest(session, uberadmin)
    # A problem whose stored strategy disagrees with its validator rows: standard,
    # yet carrying none -- inference would call it standard too, so make the
    # disagreement the other way round by giving it no validator while the seeded
    # one has an active revision. The pair proves both values survive verbatim.
    standard = Problem(
        contest_id=contest.id,
        title="Standard, no validator",
        ordinal=2,
        color="#00ff00",
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(standard)
    await session.flush()
    save_md_statement(standard.id, "# Standard\n", settings.PROBLEM_STATEMENT_DIR)
    await session.commit()
    zip_path = await _export(session, contest, tmp_path)

    restored = await _restore(session, zip_path, uberadmin, slug="round-trip", name="Round trip")

    ordered = select(Problem).where(Problem.contest_id == restored.id).order_by(Problem.ordinal)
    result = await session.execute(ordered)
    restored_problems = result.scalars().all()
    assert [p.validator_type for p in restored_problems] == [
        ProblemValidatorType.INTERACTIVE,
        ProblemValidatorType.STANDARD,
    ]
