from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import pytest_asyncio
import valkey.asyncio as aivalkey
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import JudgmentStatus, RoleEnum, TaskType, Verdict
from shared.services.lock_service import get_lock
from web.models.language import Language
from web.models.problem import Problem
from web.models.site import Site
from web.models.submission import Submission, SubmissionJudgment, SubmissionJudgmentAudit
from web.models.users import UberAdmin, User
from web.services.judging_service import (
    AlreadyConfirmedError,
    ChiefJudgeRemovalBlockedError,
    JudgmentNotDoneError,
    JudgmentNotReadyError,
    SameVerdictError,
    create_balloon_task_if_needed,
    get_chief_judge_admin_panel,
    get_judging_history,
    override_verdict,
    queue_limit_change_batch_rejudges,
    remove_chief_judge,
    set_chief_judge,
)
from web.services.judging_service import (
    acquire_submission_review as _acquire_submission_review,
)
from web.services.judging_service import (
    confirm_verdict as _confirm_verdict,
)
from web.services.judging_service import (
    release_submission_review as _release_submission_review,
)
from web.services.problem_service import (
    changed_effective_limits,
    create_problem_limit_change_batch,
    problem_fallback_limits,
)
from web.services.site_service import normalize_site_name_key

_LOCK_CLIENT: aivalkey.Valkey | None = None


@pytest_asyncio.fixture(autouse=True)
async def _install_lock_client(valkey_client: aivalkey.Valkey) -> None:
    global _LOCK_CLIENT
    _LOCK_CLIENT = valkey_client


async def acquire_submission_review(session: AsyncSession, judgment: SubmissionJudgment, actor: User, contest):
    assert _LOCK_CLIENT is not None
    return await _acquire_submission_review(session, judgment, actor, contest, _LOCK_CLIENT)


async def confirm_verdict(session: AsyncSession, submission_id: str, verdict: Verdict, judge: User, contest):
    assert _LOCK_CLIENT is not None
    return await _confirm_verdict(session, submission_id, verdict, judge, contest, _LOCK_CLIENT)


async def release_submission_review(
    session: AsyncSession,
    judgment: SubmissionJudgment,
    actor: User | UberAdmin,
    contest,
    *,
    force: bool = False,
):
    assert _LOCK_CLIENT is not None
    return await _release_submission_review(session, judgment, actor, contest, _LOCK_CLIENT, force=force)


async def _make_language(session: AsyncSession, language_id: str = "python3", name: str = "Python 3.14") -> Language:
    language = Language(
        id=language_id,
        name=name,
        icon="python",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["python3", "-m", "py_compile", "/sandbox/source.py"],
        run_cmd=["python3", "-u", "/sandbox/source.py"],
        source_filename="source.py",
        artifact_path="/sandbox/source.py",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(language)
    await session.flush()
    return language


async def _make_user(
    session: AsyncSession,
    contest,
    uberadmin: UberAdmin,
    username: str,
    fullname: str,
    role: RoleEnum,
) -> User:
    user = User(
        username=username,
        fullname=fullname,
        role=role,
        contest_id=contest.id,
        created_by_uberadmin_id=uberadmin.id,
    )
    user.password = "TestPass1!"
    session.add(user)
    await session.flush()
    return user


async def _make_site(session: AsyncSession, contest_id: str, name: str) -> Site:
    site = Site(
        sitename=name,
        sitename_normalized=normalize_site_name_key(name),
        contest_id=contest_id,
    )
    session.add(site)
    await session.flush()
    return site


async def _make_submission_with_judgment(
    session: AsyncSession,
    *,
    problem: Problem,
    team: User,
    language: Language,
    status: JudgmentStatus,
    autojudge_verdict: Verdict | None = None,
    final_verdict: Verdict | None = None,
    created_at: datetime | None = None,
    timestamp_minutes: int = 0,
) -> tuple[Submission, SubmissionJudgment]:
    from uuid import uuid4

    source_code = f"print('{uuid4().hex}')\n"
    submission = Submission(
        problem_id=problem.id,
        team_id=team.id,
        language_id=language.id,
        source_code=source_code,
        source_hash=hashlib.sha256(source_code.encode("utf-8")).hexdigest(),
        source_size_bytes=len(source_code.encode("utf-8")),
        timestamp_seconds=timestamp_minutes * 60,
    )
    session.add(submission)
    await session.flush()

    judgment = SubmissionJudgment(
        submission_id=submission.id,
        status=status,
        autojudge_verdict=autojudge_verdict,
        final_verdict=final_verdict,
        created_at=created_at or datetime.now(UTC),
        timestamp_seconds=timestamp_minutes * 60,
    )
    session.add(judgment)
    await session.flush()

    # The before_flush ORM hook recalculates final_verdict from confirmations, always
    # returning None for new judgments with no confirmations.  Persist the intended
    # value directly via Core SQL so the DB record reflects what the test set up.
    if final_verdict is not None and judgment.final_verdict != final_verdict:
        from sqlalchemy.orm import attributes as _orm_attrs

        from shared.db_schema import submission_judgments as sj_table

        await session.execute(
            update(sj_table).where(sj_table.c.id == judgment.id).values(final_verdict=final_verdict.value)
        )
        _orm_attrs.set_committed_value(judgment, "final_verdict", final_verdict)

    return submission, judgment


async def test_set_chief_judge_sets_selected_judge(
    session: AsyncSession,
    running_contest,
    admin_user: User,
    judge_user: User,
) -> None:
    running_contest.owner_user_id = admin_user.id
    await session.flush()

    updated = await set_chief_judge(session, running_contest, judge_user.id, admin_user)

    assert updated.chief_judge_id == judge_user.id


async def test_set_chief_judge_clears_assignment_when_blank(
    session: AsyncSession,
    running_contest,
    admin_user: User,
    judge_user: User,
) -> None:
    running_contest.owner_user_id = admin_user.id
    running_contest.chief_judge_id = judge_user.id
    await session.flush()

    updated = await set_chief_judge(session, running_contest, None, admin_user)

    assert updated.chief_judge_id is None


async def test_set_chief_judge_rejects_non_owner_non_uberadmin(
    session: AsyncSession,
    running_contest,
    admin_user: User,
    another_judge_user: User,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await set_chief_judge(session, running_contest, another_judge_user.id, admin_user)

    assert exc_info.value.status_code == 403


async def test_remove_chief_judge_succeeds_without_override_history(
    session: AsyncSession,
    running_contest,
    admin_user: User,
    judge_user: User,
) -> None:
    running_contest.owner_user_id = admin_user.id
    running_contest.chief_judge_id = judge_user.id
    await session.flush()

    updated = await remove_chief_judge(session, running_contest, admin_user)

    assert updated.chief_judge_id is None


async def test_remove_chief_judge_blocked_after_override(
    session: AsyncSession,
    running_contest,
    admin_user: User,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
    uberadmin: UberAdmin,
) -> None:
    running_contest.owner_user_id = admin_user.id
    running_contest.chief_judge_id = judge_user.id
    language = await _make_language(session)
    submission, judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.WA,
    )
    from web.models.submission import HumanSubmissionConfirmation as HSC

    session.add(
        HSC(
            judgment=judgment,
            judge_id=judge_user.id,
            confirmed_verdict=Verdict.WA,
            is_chief_confirmation=True,
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()
    await override_verdict(
        session,
        submission.id,
        Verdict.AC,
        "Chief judge corrected the final verdict.",
        judge_user,
        running_contest,
    )
    await session.flush()

    with pytest.raises(ChiefJudgeRemovalBlockedError):
        await remove_chief_judge(session, running_contest, admin_user)

    assert running_contest.chief_judge_id == judge_user.id


async def test_get_chief_judge_admin_panel_reports_current_and_removability(
    session: AsyncSession,
    running_contest,
    admin_user: User,
    judge_user: User,
) -> None:
    running_contest.owner_user_id = admin_user.id
    running_contest.chief_judge_id = judge_user.id
    await session.flush()

    panel = await get_chief_judge_admin_panel(session, running_contest)

    assert panel.current_chief_judge is not None
    assert panel.current_chief_judge.id == judge_user.id
    assert any(judge.id == judge_user.id for judge in panel.judges)
    assert panel.can_remove is True


async def test_get_chief_judge_admin_panel_eager_loads_judge_sites(
    session: AsyncSession,
    running_contest,
    admin_user: User,
    judge_user: User,
    another_judge_user: User,
) -> None:
    running_contest.owner_user_id = admin_user.id
    running_contest.chief_judge_id = judge_user.id
    site = await _make_site(session, running_contest.id, "Campus A")
    judge_user.site_id = site.id
    judge_user.site = site
    another_judge_user.site_id = site.id
    another_judge_user.site = site
    await session.flush()

    panel = await get_chief_judge_admin_panel(session, running_contest)

    assert panel.current_chief_judge is not None
    assert panel.current_chief_judge.site is not None
    assert panel.current_chief_judge.site.sitename == "Campus A"
    assert all(judge.site is not None for judge in panel.judges)


async def test_override_verdict_creates_override_and_updates_final_verdict(
    session: AsyncSession,
    running_contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    running_contest.chief_judge_id = judge_user.id
    language = await _make_language(session)
    submission, judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.WA,
        final_verdict=None,
    )
    from web.models.submission import HumanSubmissionConfirmation as HSC

    session.add(
        HSC(
            judgment=judgment,
            judge_id=judge_user.id,
            confirmed_verdict=Verdict.WA,
            is_chief_confirmation=True,
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()

    override = await override_verdict(
        session,
        submission.id,
        Verdict.AC,
        "Chief judge reviewed the run and accepted the answer.",
        judge_user,
        running_contest,
    )

    assert override.submission_id == submission.id
    assert override.judgment_id == judgment.id
    assert override.original_verdict == Verdict.WA
    assert override.new_verdict == Verdict.AC
    assert judgment.final_verdict == Verdict.AC


async def test_override_verdict_rejects_non_done_judgment(
    session: AsyncSession,
    running_contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    running_contest.chief_judge_id = judge_user.id
    language = await _make_language(session)
    submission, _judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.JUDGING,
        autojudge_verdict=None,
        final_verdict=None,
    )

    with pytest.raises(JudgmentNotDoneError):
        await override_verdict(
            session,
            submission.id,
            Verdict.AC,
            "Trying to override before the judgment has completed.",
            judge_user,
            running_contest,
        )


async def test_override_verdict_requires_existing_final_verdict(
    session: AsyncSession,
    running_contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    running_contest.chief_judge_id = judge_user.id
    language = await _make_language(session)
    submission, _judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.WA,
        final_verdict=None,
    )

    with pytest.raises(HTTPException) as exc_info:
        await override_verdict(
            session,
            submission.id,
            Verdict.AC,
            "Trying to override before a final verdict exists.",
            judge_user,
            running_contest,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "The active judgment does not have a final verdict yet."


async def test_override_verdict_rejects_same_verdict(
    session: AsyncSession,
    running_contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    running_contest.chief_judge_id = judge_user.id
    language = await _make_language(session)
    submission, judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.WA,
        final_verdict=None,
    )
    from web.models.submission import HumanSubmissionConfirmation as HSC

    session.add(
        HSC(
            judgment=judgment,
            judge_id=judge_user.id,
            confirmed_verdict=Verdict.WA,
            is_chief_confirmation=True,
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()

    with pytest.raises(SameVerdictError):
        await override_verdict(
            session,
            submission.id,
            Verdict.WA,
            "Reasserting the same verdict should be rejected.",
            judge_user,
            running_contest,
        )


async def test_create_balloon_task_if_needed_skips_post_freeze_accepts(
    session: AsyncSession,
    running_contest,
    team_user: User,
    contest_problem: Problem,
) -> None:
    running_contest.stop_updating_scoreboard = 1
    language = await _make_language(session)
    submission, _judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.AC,
        final_verdict=Verdict.AC,
        timestamp_minutes=75,
    )

    task = await create_balloon_task_if_needed(session, submission.id, running_contest)

    assert task is None


async def test_create_balloon_task_if_needed_creates_first_balloon_for_first_problem_solve(
    session: AsyncSession,
    running_contest,
    team_user: User,
    contest_problem: Problem,
) -> None:
    running_contest.stop_updating_scoreboard = 60
    language = await _make_language(session)
    submission, _judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.AC,
        final_verdict=Verdict.AC,
        timestamp_minutes=45,
    )

    task = await create_balloon_task_if_needed(session, submission.id, running_contest)

    assert task is not None
    assert task.type == TaskType.FIRST_BALLOON


async def test_create_balloon_task_if_needed_creates_normal_balloon_after_first_solve(
    session: AsyncSession,
    running_contest,
    team_user: User,
    another_team_user: User,
    contest_problem: Problem,
) -> None:
    running_contest.stop_updating_scoreboard = 60
    language = await _make_language(session)
    first_submission, _first_judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=another_team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.AC,
        final_verdict=Verdict.AC,
        timestamp_minutes=10,
    )
    second_submission, _second_judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.AC,
        final_verdict=Verdict.AC,
        timestamp_minutes=20,
    )

    first_task = await create_balloon_task_if_needed(session, first_submission.id, running_contest)
    second_task = await create_balloon_task_if_needed(session, second_submission.id, running_contest)

    assert first_task is not None
    assert first_task.type == TaskType.FIRST_BALLOON
    assert second_task is not None
    assert second_task.type == TaskType.BALLOON


async def test_get_judging_history_forbidden_for_team_but_allowed_for_staff(
    session: AsyncSession,
    running_contest,
    team_user: User,
    contest_problem: Problem,
    uberadmin: UberAdmin,
) -> None:
    language = await _make_language(session)
    submission, _judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.AC,
        final_verdict=Verdict.AC,
    )
    staff_user = await _make_user(session, running_contest, uberadmin, "staff_a", "Staff A", RoleEnum.STAFF)

    with pytest.raises(HTTPException) as team_exc:
        await get_judging_history(session, submission.id, team_user, running_contest)
    assert team_exc.value.status_code == 403

    history = await get_judging_history(session, submission.id, staff_user, running_contest)
    assert history.submission_id == UUID(submission.id)
    assert len(history.entries) >= 1


async def test_get_judging_history_filters_status_only_rows_and_skips_override_model_hook_duplicate(
    session: AsyncSession,
    running_contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    running_contest.chief_judge_id = judge_user.id
    language = await _make_language(session)
    base_time = datetime.now(UTC)
    created_at = base_time - timedelta(minutes=3)
    submission, judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.WA,
        created_at=created_at,
    )
    created_audit = next(audit for audit in judgment.audit_logs if audit.event_type == "created")
    created_audit.created_at = created_at

    session.add(
        SubmissionJudgmentAudit(
            judgment=judgment,
            submission_id=submission.id,
            actor_user_id=judge_user.id,
            event_source="WEB",
            event_type="updated",
            from_status=JudgmentStatus.DISPATCHED,
            to_status=JudgmentStatus.JUDGING,
            from_verdict=Verdict.WA,
            to_verdict=Verdict.WA,
            created_at=base_time - timedelta(minutes=2),
        )
    )
    session.add(
        SubmissionJudgmentAudit(
            judgment=judgment,
            submission_id=submission.id,
            actor_user_id=judge_user.id,
            event_source="WEB",
            event_type="updated",
            from_status=JudgmentStatus.DONE,
            to_status=JudgmentStatus.DONE,
            from_verdict=Verdict.WA,
            to_verdict=Verdict.RE,
            created_at=base_time - timedelta(minutes=1, seconds=30),
        )
    )
    from web.models.submission import HumanSubmissionConfirmation as HSC

    session.add(
        HSC(
            judgment=judgment,
            judge_id=judge_user.id,
            confirmed_verdict=Verdict.WA,
            is_chief_confirmation=True,
            created_at=base_time - timedelta(minutes=1),
        )
    )
    await session.flush()

    await override_verdict(
        session,
        submission.id,
        Verdict.AC,
        "Chief judge accepted the run after manual review.",
        judge_user,
        running_contest,
    )
    await session.commit()

    history = await get_judging_history(session, submission.id, judge_user, running_contest)

    # Chief confirmation appears explicitly between the override and the rejudge entries.
    assert [entry.kind for entry in history.entries] == [
        "override",
        "chief_confirmation",
        "rejudge",
        "auto",
    ]
    assert history.entries[0].verdict == Verdict.AC
    assert history.entries[0].reason == "Chief judge accepted the run after manual review."
    assert history.entries[2].kind == "rejudge"
    assert history.entries[2].verdict == Verdict.RE
    assert history.entries[3].kind == "auto"
    assert history.entries[3].status == JudgmentStatus.DONE


async def test_double_override_final_verdict_reflects_latest_override(
    session: AsyncSession,
    running_contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    """A second override (AC→WA) must set final_verdict back to WA, not leave it as AC."""
    running_contest.chief_judge_id = judge_user.id
    language = await _make_language(session)
    submission, judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.WA,
    )
    from web.models.submission import HumanSubmissionConfirmation as HSC

    session.add(
        HSC(
            judgment=judgment,
            judge_id=judge_user.id,
            confirmed_verdict=Verdict.WA,
            is_chief_confirmation=True,
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()

    # First override: WA → AC
    await override_verdict(
        session,
        submission.id,
        Verdict.AC,
        "Chief judge accepted the run after manual review.",
        judge_user,
        running_contest,
    )
    await session.commit()

    assert judgment.final_verdict == Verdict.AC

    # Second override: AC → WA (in a new effective state, same session after commit)
    await override_verdict(
        session,
        submission.id,
        Verdict.WA,
        "Chief judge reversed the decision after further review.",
        judge_user,
        running_contest,
    )
    await session.commit()

    assert judgment.final_verdict == Verdict.WA


async def test_two_non_chief_confirmations_matching_autojudge_finalize(
    session: AsyncSession,
    running_contest,
    team_user: User,
    judge_user: User,
    another_judge_user: User,
    contest_problem: Problem,
) -> None:
    """Two non-chief judges agreeing with autojudge_verdict should set final_verdict."""
    running_contest.autojudge_only = False
    await session.flush()
    language = await _make_language(session)
    submission, judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.WA,
    )

    from web.models.submission import HumanSubmissionConfirmation as HSC

    session.add(
        HSC(
            judgment=judgment,
            judge_id=judge_user.id,
            confirmed_verdict=Verdict.WA,
            is_chief_confirmation=False,
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()
    assert judgment.final_verdict is None  # only 1 confirmation so far

    session.add(
        HSC(
            judgment=judgment,
            judge_id=another_judge_user.id,
            confirmed_verdict=Verdict.WA,
            is_chief_confirmation=False,
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()
    assert judgment.final_verdict == Verdict.WA


async def test_two_non_chief_confirmations_disagreeing_do_not_finalize(
    session: AsyncSession,
    running_contest,
    team_user: User,
    judge_user: User,
    another_judge_user: User,
    contest_problem: Problem,
) -> None:
    """If one judge disagrees with autojudge_verdict, final_verdict stays None."""
    running_contest.autojudge_only = False
    await session.flush()
    language = await _make_language(session)
    submission, judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.WA,
    )

    from web.models.submission import HumanSubmissionConfirmation as HSC

    session.add(
        HSC(
            judgment=judgment,
            judge_id=judge_user.id,
            confirmed_verdict=Verdict.WA,
            is_chief_confirmation=False,
            created_at=datetime.now(UTC),
        )
    )
    session.add(
        HSC(
            judgment=judgment,
            judge_id=another_judge_user.id,
            confirmed_verdict=Verdict.AC,
            is_chief_confirmation=False,
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()
    assert judgment.final_verdict is None


async def test_chief_confirmation_finalizes_immediately(
    session: AsyncSession,
    running_contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    """A chief confirmation (any verdict) must set final_verdict immediately."""
    running_contest.autojudge_only = False
    await session.flush()
    language = await _make_language(session)
    submission, judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.WA,
    )

    from web.models.submission import HumanSubmissionConfirmation as HSC

    session.add(
        HSC(
            judgment=judgment,
            judge_id=judge_user.id,
            confirmed_verdict=Verdict.AC,
            is_chief_confirmation=True,
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()
    assert judgment.final_verdict == Verdict.AC


async def test_double_override_separate_sessions(
    session: AsyncSession,
    engine,
    running_contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    """Same as above but with the second override in a separate session, simulating two HTTP requests."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlalchemy.future import select as sa_select

    running_contest.chief_judge_id = judge_user.id
    await session.flush()
    language = await _make_language(session)
    submission, judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.WA,
    )
    from web.models.submission import HumanSubmissionConfirmation as HSC

    session.add(
        HSC(
            judgment=judgment,
            judge_id=judge_user.id,
            confirmed_verdict=Verdict.WA,
            is_chief_confirmation=True,
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()

    # First override: WA → AC — committed so it's visible to another session
    await override_verdict(
        session,
        submission.id,
        Verdict.AC,
        "Chief judge accepted the run after manual review.",
        judge_user,
        running_contest,
    )
    await session.commit()
    assert judgment.final_verdict == Verdict.AC

    submission_id = submission.id
    contest_id = running_contest.id
    judge_id = judge_user.id

    # Second override: AC → WA — in a completely fresh session (new HTTP request)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session2:
        contest2 = await session2.get(type(running_contest), contest_id)
        judge2 = await session2.get(type(judge_user), judge_id)
        await override_verdict(session2, submission_id, Verdict.WA, "Chief reversed decision.", judge2, contest2)
        await session2.commit()

    # Verify DB state in a third fresh session
    async with factory() as verify:
        j = (
            await verify.execute(sa_select(SubmissionJudgment).where(SubmissionJudgment.submission_id == submission_id))
        ).scalar_one()
        assert j.final_verdict == Verdict.WA, f"Expected WA after second override, got {j.final_verdict}"


async def test_two_non_chief_confirmations_no_autojudge_verdict_do_not_finalize(
    session: AsyncSession,
    running_contest,
    team_user: User,
    judge_user: User,
    another_judge_user: User,
    contest_problem: Problem,
) -> None:
    """Two agreeing non-chief judges cannot finalize when autojudge_verdict is None."""
    running_contest.autojudge_only = False
    await session.flush()
    language = await _make_language(session)

    # Create judgment with a placeholder autojudge_verdict to pass ORM validation,
    # then wipe it via raw SQL so we can test the autojudge_verdict=None path.
    submission, judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.WA,
    )

    # Save the judgment ID before we expire the object from the identity map.
    judgment_id = judgment.id

    # Bypass the ORM validation hook by updating autojudge_verdict to NULL directly in SQL.
    await session.execute(
        update(SubmissionJudgment).where(SubmissionJudgment.id == judgment_id).values(autojudge_verdict=None)
    )
    # Reload the judgment from DB so autojudge_verdict is None in memory.
    await session.refresh(judgment)

    from web.models.submission import HumanSubmissionConfirmation as HSC

    session.add(
        HSC(
            judgment_id=judgment_id,
            judge_id=judge_user.id,
            confirmed_verdict=Verdict.WA,
            is_chief_confirmation=False,
            created_at=datetime.now(UTC),
        )
    )
    session.add(
        HSC(
            judgment_id=judgment_id,
            judge_id=another_judge_user.id,
            confirmed_verdict=Verdict.WA,
            is_chief_confirmation=False,
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()
    assert judgment.final_verdict is None


async def test_confirm_verdict_two_judges_matching_autojudge_sets_final_verdict(
    session: AsyncSession,
    running_contest,
    team_user: User,
    judge_user: User,
    another_judge_user: User,
    contest_problem: Problem,
) -> None:
    """Two judges confirming autojudge_verdict should set final_verdict."""
    running_contest.autojudge_only = False
    await session.flush()
    language = await _make_language(session)
    submission, judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.WA,
    )

    await acquire_submission_review(session, judgment, judge_user, running_contest)
    await confirm_verdict(session, submission.id, Verdict.WA, judge_user, running_contest)
    assert judgment.final_verdict is None  # only one confirmation so far

    await acquire_submission_review(session, judgment, another_judge_user, running_contest)
    await confirm_verdict(session, submission.id, Verdict.WA, another_judge_user, running_contest)
    assert judgment.final_verdict == Verdict.WA


async def test_confirm_verdict_chief_judge_sets_is_chief_confirmation_and_finalizes(
    session: AsyncSession,
    running_contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    """Chief judge confirmation is marked is_chief_confirmation=True and finalizes immediately."""
    running_contest.autojudge_only = False
    running_contest.chief_judge_id = judge_user.id
    await session.flush()
    language = await _make_language(session)
    submission, judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.WA,
    )

    await acquire_submission_review(session, judgment, judge_user, running_contest)
    confirmation = await confirm_verdict(session, submission.id, Verdict.AC, judge_user, running_contest)

    assert confirmation.is_chief_confirmation is True
    assert judgment.final_verdict == Verdict.AC


async def test_acquire_submission_review_persists_lock_in_valkey(
    session: AsyncSession,
    running_contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    running_contest.autojudge_only = False
    await session.flush()
    language = await _make_language(session)
    _submission, judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.WA,
    )

    await acquire_submission_review(session, judgment, judge_user, running_contest)

    assert _LOCK_CLIENT is not None
    lock = await get_lock(
        _LOCK_CLIENT,
        kind="review",
        contest_id=running_contest.id,
        resource_id=judgment.id,
    )
    assert lock is not None
    assert lock.holder_id == judge_user.id


async def test_zero_review_timeout_locks_until_contest_end(
    session: AsyncSession,
    running_contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    running_contest.autojudge_only = False
    running_contest.review_timeout_minutes = 0
    await session.flush()
    language = await _make_language(session)
    _submission, judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.WA,
    )

    await acquire_submission_review(session, judgment, judge_user, running_contest)

    assert _LOCK_CLIENT is not None
    lock = await get_lock(
        _LOCK_CLIENT,
        kind="review",
        contest_id=running_contest.id,
        resource_id=judgment.id,
    )
    assert lock is not None
    assert abs((lock.expires_at - running_contest.end_time).total_seconds()) <= 2


async def test_release_submission_review_removes_lock_from_valkey(
    session: AsyncSession,
    running_contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    running_contest.autojudge_only = False
    await session.flush()
    language = await _make_language(session)
    _submission, judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.WA,
    )

    await acquire_submission_review(session, judgment, judge_user, running_contest)
    await release_submission_review(session, judgment, judge_user, running_contest)

    assert _LOCK_CLIENT is not None
    assert (
        await get_lock(
            _LOCK_CLIENT,
            kind="review",
            contest_id=running_contest.id,
            resource_id=judgment.id,
        )
        is None
    )


async def test_confirm_verdict_removes_lock_from_valkey(
    session: AsyncSession,
    running_contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    running_contest.autojudge_only = False
    running_contest.chief_judge_id = judge_user.id
    await session.flush()
    language = await _make_language(session)
    submission, judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.AC,
    )

    await acquire_submission_review(session, judgment, judge_user, running_contest)
    await confirm_verdict(session, submission.id, Verdict.AC, judge_user, running_contest)

    assert _LOCK_CLIENT is not None
    assert (
        await get_lock(
            _LOCK_CLIENT,
            kind="review",
            contest_id=running_contest.id,
            resource_id=judgment.id,
        )
        is None
    )


async def test_confirm_verdict_raises_already_confirmed_on_duplicate(
    session: AsyncSession,
    running_contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    """Calling confirm_verdict twice for the same judge raises AlreadyConfirmedError."""
    running_contest.autojudge_only = False
    await session.flush()
    language = await _make_language(session)
    submission, judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.WA,
    )

    await acquire_submission_review(session, judgment, judge_user, running_contest)
    await confirm_verdict(session, submission.id, Verdict.WA, judge_user, running_contest)

    with pytest.raises(AlreadyConfirmedError):
        await confirm_verdict(session, submission.id, Verdict.WA, judge_user, running_contest)


async def test_confirm_verdict_raises_not_ready_when_judgment_not_done(
    session: AsyncSession,
    running_contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    """Confirming a judgment that is not DONE raises JudgmentNotReadyError."""
    running_contest.autojudge_only = False
    await session.flush()
    language = await _make_language(session)
    submission, _judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.JUDGING,
        autojudge_verdict=None,
    )

    with pytest.raises(JudgmentNotReadyError):
        await confirm_verdict(session, submission.id, Verdict.WA, judge_user, running_contest)


async def test_queue_limit_change_batch_rejudges_marks_rows_queued_and_stale(
    session: AsyncSession,
    running_contest,
    admin_user: User,
    team_user: User,
    contest_problem: Problem,
) -> None:
    language = await _make_language(session)
    active_submission, active_judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.TLE,
        final_verdict=Verdict.TLE,
    )
    stale_submission, stale_judgment = await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.MLE,
        final_verdict=Verdict.MLE,
    )

    changed = changed_effective_limits(
        contest_problem,
        [language],
        before_overrides={},
        after_overrides={
            language.id: {
                "time_limit_ms": contest_problem.time_limit_ms + 500,
                "memory_limit_kb": contest_problem.memory_limit_kb,
                "pids_limit": contest_problem.pids_limit,
                "output_limit_in_bytes": "",
                "repetitions": 1,
            }
        },
        before_fallback=problem_fallback_limits(contest_problem),
        after_fallback=problem_fallback_limits(contest_problem),
    )
    batch = await create_problem_limit_change_batch(
        session,
        running_contest,
        contest_problem,
        admin_user,
        changed,
    )

    # Add a newer judgment for stale_submission AFTER batch creation so the batch
    # holds stale_judgment.id as original_judgment_id; the ID mismatch triggers STALE.
    # created_at is set explicitly later than stale_judgment so get_active_judgment
    # reliably picks third_judgment (max by created_at).
    from sqlalchemy.orm import attributes as _orm_attrs

    from shared.db_schema import submission_judgments as _sj_table

    third_judgment = SubmissionJudgment(
        submission_id=stale_submission.id,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.AC,
        final_verdict=Verdict.AC,
        timestamp_seconds=90,
        created_at=stale_judgment.created_at + timedelta(seconds=1),
    )
    session.add(third_judgment)
    await session.flush()
    await session.execute(
        update(_sj_table).where(_sj_table.c.id == third_judgment.id).values(final_verdict=Verdict.AC.value)
    )
    _orm_attrs.set_committed_value(third_judgment, "final_verdict", Verdict.AC)

    # Expire the already-loaded judgments collection so the selectinload inside
    # queue_limit_change_batch_rejudges re-fetches from DB and sees third_judgment.
    session.expire(stale_submission, ["judgments"])

    assert batch is not None
    new_judgments = await queue_limit_change_batch_rejudges(
        session,
        batch,
        running_contest,
        admin_user,
        None,
    )

    assert len(new_judgments) == 1
    assert new_judgments[0].submission_id == active_submission.id

    row_by_submission = {row.submission_id: row for row in batch.submissions}
    assert row_by_submission[active_submission.id].rejudge_status == "QUEUED"
    assert row_by_submission[active_submission.id].queued_judgment_id == new_judgments[0].id
    assert row_by_submission[stale_submission.id].rejudge_status == "STALE"

    refreshed_active = await session.get(SubmissionJudgment, active_judgment.id)
    refreshed_stale = await session.get(SubmissionJudgment, stale_judgment.id)
    assert refreshed_active is not None and refreshed_active.status == JudgmentStatus.SUPERSEDED
    assert refreshed_stale is not None and refreshed_stale.status == JudgmentStatus.DONE


async def test_queue_limit_change_batch_rejudges_requires_admin_scope(
    session: AsyncSession,
    running_contest,
    judge_user: User,
    team_user: User,
    contest_problem: Problem,
) -> None:
    language = await _make_language(session)
    await _make_submission_with_judgment(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.TLE,
        final_verdict=Verdict.TLE,
    )
    changed = changed_effective_limits(
        contest_problem,
        [language],
        before_overrides={},
        after_overrides={
            language.id: {
                "time_limit_ms": contest_problem.time_limit_ms + 500,
                "memory_limit_kb": contest_problem.memory_limit_kb,
                "pids_limit": contest_problem.pids_limit,
                "output_limit_in_bytes": "",
                "repetitions": 1,
            }
        },
        before_fallback=problem_fallback_limits(contest_problem),
        after_fallback=problem_fallback_limits(contest_problem),
    )
    batch = await create_problem_limit_change_batch(
        session,
        running_contest,
        contest_problem,
        judge_user,
        changed,
    )

    assert batch is not None
    with pytest.raises(HTTPException) as exc_info:
        await queue_limit_change_batch_rejudges(
            session,
            batch,
            running_contest,
            judge_user,
            None,
        )

    assert exc_info.value.status_code == 403
