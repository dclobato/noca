#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Service tests for Arena sample interactions and the interactive TC invariant."""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings as arena_settings
from arena.models.arena_problems import ArenaProblem, ArenaProblemCustomValidator
from arena.models.arena_users import ArenaUser
from arena.services import (
    admin_problem_interaction_pending,
    admin_problem_interaction_service,
    admin_problem_service,
    admin_problem_tc_service,
)
from shared.enumerations import ArenaRole, CustomValidatorActiveState
from shared.services.sample_interactions import MAX_SAMPLE_INTERACTIONS, parse_interaction_text
from web.models.language import Language


async def _create_user(session: AsyncSession) -> ArenaUser:
    """Create an Arena judge who can own problems."""
    user = ArenaUser(
        nome="Interaction Author",
        email_normalizado=f"author-{uuid.uuid4().hex[:8]}@test.example",
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


async def _create_language(session: AsyncSession) -> Language:
    """Create an active language a validator can be written in."""
    language = Language(
        id=f"si-test-{uuid.uuid4().hex[:8]}",
        name="Interaction Test Language",
        icon="test",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["true"],
        run_cmd=["true"],
        source_filename="main.txt",
        artifact_path="/sandbox/main.txt",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(language)
    await session.flush()
    return language


async def _problem(session: AsyncSession, owner_id: str, *, interactive: bool = True) -> ArenaProblem:
    """Create an Arena problem, optionally with a configured validator."""
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=owner_id,
        title="Guess The Number",
        author=None,
        author_is_owner=True,
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
        license=None,
        category_ids=[],
    )
    if interactive:
        language = await _create_language(session)
        session.add(
            ArenaProblemCustomValidator(
                problem_id=problem.id,
                active_language_id=language.id,
                active_source="print('validator')\n",
                active_state=CustomValidatorActiveState.VALID,
                active_validated_at=datetime.now(UTC),
            )
        )
        await session.flush()
        await session.refresh(problem, attribute_names=["custom_validator"])
    return problem


async def _add_case(session: AsyncSession, problem: ArenaProblem, *, is_sample: bool = False) -> None:
    """Append one test case to a problem and write its files."""
    _tc, write_files = await admin_problem_tc_service.create_testcase(
        session,
        problem,
        input_content="7\n",
        output_content="7\n",
        is_sample=is_sample,
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )
    await session.flush()
    write_files()


# ── ordering and the max-5 cap ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_interactions_append_in_order(session: AsyncSession) -> None:
    """Each new interaction lands at the end of the problem's ordered list."""
    owner = await _create_user(session)
    problem = await _problem(session, owner.id)

    for text in ("> 1", "> 2", "> 3"):
        await admin_problem_interaction_service.create_interaction(
            session, problem, transcript=parse_interaction_text(text)
        )
    await session.flush()

    rows = await admin_problem_interaction_service.list_interactions(session, problem.id)
    assert [row.ordinal for row in rows] == [1, 2, 3]


@pytest.mark.asyncio
async def test_interactions_are_capped(session: AsyncSession) -> None:
    """A problem cannot hold more interactions than the cap allows."""
    owner = await _create_user(session)
    problem = await _problem(session, owner.id)

    for index in range(MAX_SAMPLE_INTERACTIONS):
        await admin_problem_interaction_service.create_interaction(
            session, problem, transcript=parse_interaction_text(f"> {index}")
        )
        await session.flush()

    with pytest.raises(ValueError, match="at most"):
        await admin_problem_interaction_service.create_interaction(
            session, problem, transcript=parse_interaction_text("> too many")
        )


@pytest.mark.asyncio
async def test_hidden_interactions_still_count_towards_the_cap(session: AsyncSession) -> None:
    """A hidden interaction can be un-hidden later, so it must not free a slot."""
    owner = await _create_user(session)
    problem = await _problem(session, owner.id)

    for index in range(MAX_SAMPLE_INTERACTIONS):
        await admin_problem_interaction_service.create_interaction(
            session, problem, transcript=parse_interaction_text(f"> {index}")
        )
        await session.flush()
    await admin_problem_interaction_service.hide_interactions(session, problem.id)

    # The visible list is empty, but the slots are still spoken for.
    assert await admin_problem_interaction_service.list_interactions(session, problem.id) == []
    with pytest.raises(ValueError, match="at most"):
        await admin_problem_interaction_service.create_interaction(
            session, problem, transcript=parse_interaction_text("> nope")
        )


@pytest.mark.asyncio
async def test_a_full_problem_can_swap_an_interaction_in_one_save(session: AsyncSession) -> None:
    """Removals land before additions, so the fifth slot is free for a replacement.

    An author at the cap who marks one interaction for removal must be able to add
    its replacement in the same edit: the cap is judged against the row count the
    save produces, not the one it started from.
    """
    owner = await _create_user(session)
    problem = await _problem(session, owner.id)
    for index in range(MAX_SAMPLE_INTERACTIONS):
        await admin_problem_interaction_service.create_interaction(
            session, problem, transcript=parse_interaction_text(f"> old {index}")
        )
        await session.flush()

    doomed = (await admin_problem_interaction_service.list_interactions(session, problem.id))[0]
    await admin_problem_interaction_pending.apply_pending_interactions(
        session,
        problem,
        {"si_remove_ids": doomed.id},
        [(parse_interaction_text("> replacement"), None)],
    )
    await session.flush()

    rows = await admin_problem_interaction_service.list_interactions(session, problem.id)
    assert len(rows) == MAX_SAMPLE_INTERACTIONS
    assert [row.ordinal for row in rows] == list(range(1, MAX_SAMPLE_INTERACTIONS + 1))
    lines = [row.transcript["lines"][0]["line"] for row in rows]
    assert "old 0" not in lines
    assert lines[-1] == "replacement"


@pytest.mark.asyncio
async def test_a_save_that_would_overflow_the_cap_is_rejected_before_mutating(session: AsyncSession) -> None:
    """The final count is validated up front, so a doomed save changes nothing."""
    owner = await _create_user(session)
    problem = await _problem(session, owner.id)
    for index in range(MAX_SAMPLE_INTERACTIONS):
        await admin_problem_interaction_service.create_interaction(
            session, problem, transcript=parse_interaction_text(f"> old {index}")
        )
        await session.flush()

    doomed = (await admin_problem_interaction_service.list_interactions(session, problem.id))[0]
    with pytest.raises(ValueError, match="at most"):
        # One removal frees one slot, but two additions need two.
        await admin_problem_interaction_pending.apply_pending_interactions(
            session,
            problem,
            {"si_remove_ids": doomed.id},
            [(parse_interaction_text("> a"), None), (parse_interaction_text("> b"), None)],
        )

    # The rejected save left the existing interactions untouched.
    rows = await admin_problem_interaction_service.list_interactions(session, problem.id)
    assert len(rows) == MAX_SAMPLE_INTERACTIONS
    assert [row.transcript["lines"][0]["line"] for row in rows] == [
        f"old {index}" for index in range(MAX_SAMPLE_INTERACTIONS)
    ]


@pytest.mark.asyncio
async def test_deleting_an_interaction_closes_the_ordinal_gap(session: AsyncSession) -> None:
    """Ordinals stay a dense 1..n sequence after a removal."""
    owner = await _create_user(session)
    problem = await _problem(session, owner.id)
    for text in ("> 1", "> 2", "> 3"):
        await admin_problem_interaction_service.create_interaction(
            session, problem, transcript=parse_interaction_text(text)
        )
        await session.flush()

    rows = await admin_problem_interaction_service.list_interactions(session, problem.id)
    await admin_problem_interaction_service.delete_interaction(session, rows[0])
    await session.flush()

    remaining = await admin_problem_interaction_service.list_interactions(session, problem.id)
    assert [row.ordinal for row in remaining] == [1, 2]
    assert [row.transcript["lines"][0]["line"] for row in remaining] == ["2", "3"]


@pytest.mark.asyncio
async def test_moving_an_interaction_reorders_without_gaps(session: AsyncSession) -> None:
    """A drag-reorder rewrites ordinals densely in the requested order."""
    owner = await _create_user(session)
    problem = await _problem(session, owner.id)
    for text in ("> 1", "> 2", "> 3"):
        await admin_problem_interaction_service.create_interaction(
            session, problem, transcript=parse_interaction_text(text)
        )
        await session.flush()

    rows = await admin_problem_interaction_service.list_interactions(session, problem.id)
    await admin_problem_interaction_service.move_interaction(session, rows[2], 1)
    await session.flush()

    reordered = await admin_problem_interaction_service.list_interactions(session, problem.id)
    assert [row.ordinal for row in reordered] == [1, 2, 3]
    assert [row.transcript["lines"][0]["line"] for row in reordered] == ["3", "1", "2"]


# ── hide / unhide ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hidden_interactions_resurface_when_unhidden(session: AsyncSession) -> None:
    """Interactions kept through a validator removal come back with a new validator."""
    owner = await _create_user(session)
    problem = await _problem(session, owner.id)
    await admin_problem_interaction_service.create_interaction(
        session, problem, transcript=parse_interaction_text("> kept")
    )
    await session.flush()

    assert await admin_problem_interaction_service.hide_interactions(session, problem.id) == 1
    assert await admin_problem_interaction_service.list_interactions(session, problem.id) == []

    assert await admin_problem_interaction_service.unhide_interactions(session, problem.id) == 1
    assert len(await admin_problem_interaction_service.list_interactions(session, problem.id)) == 1


@pytest.mark.asyncio
async def test_delete_all_drops_hidden_interactions_too(session: AsyncSession) -> None:
    """Choosing to drop interactions removes them whether or not they were hidden."""
    owner = await _create_user(session)
    problem = await _problem(session, owner.id)
    await admin_problem_interaction_service.create_interaction(
        session, problem, transcript=parse_interaction_text("> gone")
    )
    await session.flush()
    await admin_problem_interaction_service.hide_interactions(session, problem.id)

    assert await admin_problem_interaction_service.delete_all_interactions(session, problem.id) == 1
    assert await admin_problem_interaction_service.count_interactions(session, problem.id) == 0


# ── the interactive test-case invariant ───────────────────────────────────────


@pytest.mark.asyncio
async def test_staging_a_validator_demotes_public_test_cases(session: AsyncSession) -> None:
    """A problem that becomes interactive loses its public cases to secrecy."""
    owner = await _create_user(session)
    problem = await _problem(session, owner.id, interactive=False)
    await _add_case(session, problem, is_sample=True)
    await _add_case(session, problem, is_sample=True)

    demoted = await admin_problem_interaction_service.convert_sample_testcases_to_secret(session, problem.id)
    await session.flush()

    assert demoted == 2
    cases = await admin_problem_tc_service.list_testcases(session, problem.id)
    assert all(not case.is_sample for case in cases)


@pytest.mark.asyncio
async def test_interactive_problem_refuses_a_sample_test_case(session: AsyncSession) -> None:
    """Creating a case on an interactive problem never marks it public."""
    owner = await _create_user(session)
    problem = await _problem(session, owner.id)

    await _add_case(session, problem, is_sample=True)

    cases = await admin_problem_tc_service.list_testcases(session, problem.id)
    assert cases[0].is_sample is False


@pytest.mark.asyncio
async def test_zip_replace_forces_secret_cases_on_an_interactive_problem(session: AsyncSession) -> None:
    """A ZIP bulk-replace cannot smuggle a public case onto an interactive problem.

    The caller asks for public cases explicitly here; the service must still refuse,
    because an interactive problem presents sample interactions instead.
    """
    owner = await _create_user(session)
    problem = await _problem(session, owner.id)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("in/001.in", b"7\n")
        archive.writestr("in/002.in", b"8\n")

    _count, apply_files = await admin_problem_tc_service.replace_all_from_zip(
        session,
        problem,
        buffer.getvalue(),
        default_is_sample=True,
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )
    await session.commit()
    apply_files()

    cases = await admin_problem_tc_service.list_testcases(session, problem.id)
    assert len(cases) == 2
    assert all(not case.is_sample for case in cases)
    assert await admin_problem_interaction_service.interactive_testcase_error(session, problem.id) is None


@pytest.mark.asyncio
async def test_zip_replace_honours_the_sample_default_on_a_plain_problem(session: AsyncSession) -> None:
    """The forced-secret rule is scoped to interactive problems; plain ones are free."""
    owner = await _create_user(session)
    problem = await _problem(session, owner.id, interactive=False)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("in/001.in", b"7\n")
        archive.writestr("out/001.out", b"7\n")

    _count, apply_files = await admin_problem_tc_service.replace_all_from_zip(
        session,
        problem,
        buffer.getvalue(),
        default_is_sample=True,
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )
    await session.commit()
    apply_files()

    cases = await admin_problem_tc_service.list_testcases(session, problem.id)
    assert [case.is_sample for case in cases] == [True]


@pytest.mark.asyncio
async def test_toggle_sample_is_refused_on_an_interactive_problem(session: AsyncSession) -> None:
    """The sample/secret toggle has no meaning where every case must be secret."""
    owner = await _create_user(session)
    problem = await _problem(session, owner.id)
    await _add_case(session, problem)
    case = (await admin_problem_tc_service.list_testcases(session, problem.id))[0]

    with pytest.raises(ValueError, match="sample interactions"):
        await admin_problem_tc_service.toggle_sample(session, case)


@pytest.mark.asyncio
async def test_invariant_flags_an_interactive_problem_with_no_cases(session: AsyncSession) -> None:
    """An interactive draft with no secret case cannot be enabled or judged."""
    owner = await _create_user(session)
    problem = await _problem(session, owner.id)

    error = await admin_problem_interaction_service.interactive_testcase_error(session, problem.id)

    assert error is not None
    assert "secret test case" in error


@pytest.mark.asyncio
async def test_invariant_passes_for_a_secret_only_interactive_problem(session: AsyncSession) -> None:
    """One secret case and no public ones is a complete interactive problem."""
    owner = await _create_user(session)
    problem = await _problem(session, owner.id)
    await _add_case(session, problem)

    assert await admin_problem_interaction_service.interactive_testcase_error(session, problem.id) is None
