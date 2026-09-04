#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for admin_problem_service and admin_problem_tc_service."""

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.models.arena_problems import (
    ArenaCategory,
    ArenaProblem,
    ArenaProblemCustomValidator,
    ArenaTestCase,
)
from arena.models.arena_users import ArenaUser
from arena.services import admin_problem_service
from shared.enumerations import (
    ArenaEditorialReleasePolicy,
    ArenaRole,
    CustomValidatorActiveState,
    CustomValidatorCandidateState,
    ProblemValidatorType,
)
from web.models.language import Language


async def _make_user(
    session: AsyncSession,
    *,
    role: ArenaRole = ArenaRole.ARENA_JUDGE,
    can_edit: bool = False,
    email_suffix: str = "",
) -> ArenaUser:
    suffix = email_suffix or role.value.lower()
    user = ArenaUser(
        nome="Test Author",
        email_normalizado=f"author-{suffix}@test.example",
        password_hash="hash",
        role=role,
        can_edit=can_edit,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(1990, 1, 1),
        consentimento_responsavel=True,
        session_version=0,
    )
    session.add(user)
    await session.flush()
    return user


async def _make_language(session: AsyncSession) -> Language:
    language = Language(
        id=f"admin-problem-test-{uuid.uuid4().hex[:8]}",
        name="Admin Problem Test Language",
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


async def _make_problem(
    session: AsyncSession,
    owner_id: str,
    *,
    title: str = "My Problem",
    license: str | None = None,
    validator_type: ProblemValidatorType = ProblemValidatorType.STANDARD,
) -> ArenaProblem:
    p = await admin_problem_service.create_problem(
        session,
        caller_id=owner_id,
        title=title,
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="Hello world",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[],
        license=license,
        validator_type=validator_type,
    )
    await session.flush()
    return p


# ── create_problem ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_problem_defaults_to_disabled(session: AsyncSession) -> None:
    author = await _make_user(session)
    problem = await _make_problem(session, author.id)
    assert problem.enabled is False


@pytest.mark.asyncio
async def test_create_problem_assigns_owner_as_author_by_default(session: AsyncSession) -> None:
    owner = await _make_user(session)
    problem = await _make_problem(session, owner.id)
    assert problem.owner_id == owner.id
    assert problem.author is None
    assert problem.author_is_owner is True


@pytest.mark.asyncio
async def test_create_problem_preserves_free_text_author(session: AsyncSession) -> None:
    owner = await _make_user(session)
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=owner.id,
        title="Guest Problem",
        author="  External Author  ",
        author_is_owner=False,
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
        validator_type=ProblemValidatorType.STANDARD,
    )
    assert problem.owner_id == owner.id
    assert problem.author == "External Author"
    assert problem.author_is_owner is False


@pytest.mark.asyncio
async def test_create_problem_normalizes_optional_license(session: AsyncSession) -> None:
    """Problem licenses are trimmed, while blank values are stored as null."""
    owner = await _make_user(session)

    licensed_problem = await _make_problem(session, owner.id, license="  CC BY 4.0  ")
    unlicensed_problem = await _make_problem(session, owner.id, title="No License", license="   ")

    assert licensed_problem.license == "CC BY 4.0"
    assert unlicensed_problem.license is None


@pytest.mark.asyncio
async def test_create_problem_rejects_license_over_256_characters(session: AsyncSession) -> None:
    """The service enforces the database and form license length limit."""
    owner = await _make_user(session)

    with pytest.raises(ValueError, match="License must be at most 256 characters"):
        await _make_problem(session, owner.id, license="x" * 257)


@pytest.mark.asyncio
async def test_create_problem_requires_free_text_author(session: AsyncSession) -> None:
    owner = await _make_user(session)
    with pytest.raises(ValueError, match="Author is required"):
        await admin_problem_service.create_problem(
            session,
            caller_id=owner.id,
            title="Missing Author",
            author=" ",
            author_is_owner=False,
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
            validator_type=ProblemValidatorType.STANDARD,
        )


@pytest.mark.asyncio
async def test_create_problem_blank_title_raises(session: AsyncSession) -> None:
    author = await _make_user(session)
    with pytest.raises(ValueError, match="Title is required"):
        await admin_problem_service.create_problem(
            session,
            caller_id=author.id,
            title="   ",
            source=None,
            hide_author_show_source=False,
            time_limit_ms=1000,
            memory_limit_kb=262144,
            pids_limit=64,
            output_limit_in_bytes=65536,
            problem_statement="x",
            image_b64=None,
            image_mime=None,
            image_caption=None,
            notes=None,
            category_ids=[],
            validator_type=ProblemValidatorType.STANDARD,
        )


@pytest.mark.asyncio
async def test_create_problem_rejects_disallowed_markdown(session: AsyncSession) -> None:
    """Arena should enforce the same Markdown validation as the web module."""
    author = await _make_user(session)
    with pytest.raises(ValueError, match="Markdown statement contains disallowed content: link."):
        await admin_problem_service.create_problem(
            session,
            caller_id=author.id,
            title="Markdown Guard",
            source=None,
            hide_author_show_source=False,
            time_limit_ms=1000,
            memory_limit_kb=262144,
            pids_limit=64,
            output_limit_in_bytes=65536,
            problem_statement="[example](https://example.com)",
            image_b64=None,
            image_mime=None,
            image_caption=None,
            notes=None,
            category_ids=[],
            validator_type=ProblemValidatorType.STANDARD,
        )


# ── toggle_enabled ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_toggle_enabled_flips_flag(session: AsyncSession) -> None:
    author = await _make_user(session)
    problem = await _make_problem(session, author.id)
    assert problem.enabled is False
    await admin_problem_service.toggle_enabled(session, problem)
    assert problem.enabled is True
    await admin_problem_service.toggle_enabled(session, problem)
    assert problem.enabled is False


# ── get_problem access control ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_judge_cannot_access_other_judges_problem(session: AsyncSession) -> None:
    author_a = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    author_b = ArenaUser(
        nome="Other Judge",
        email_normalizado="other@test.example",
        password_hash="h",
        role=ArenaRole.ARENA_JUDGE,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(1990, 1, 1),
        consentimento_responsavel=True,
        session_version=0,
    )
    session.add(author_b)
    await session.flush()

    problem = await _make_problem(session, author_a.id)
    await session.flush()

    result = await admin_problem_service.get_problem(session, problem.id, caller_id=author_b.id, is_admin=False)
    assert result is None


@pytest.mark.asyncio
async def test_admin_can_access_any_problem(session: AsyncSession) -> None:
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    admin = ArenaUser(
        nome="Admin",
        email_normalizado="admin@test.example",
        password_hash="h",
        role=ArenaRole.ARENA_ADMIN,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(1990, 1, 1),
        consentimento_responsavel=True,
        session_version=0,
    )
    session.add(admin)
    await session.flush()

    problem = await _make_problem(session, judge.id)
    await session.flush()

    result = await admin_problem_service.get_problem(session, problem.id, caller_id=admin.id, is_admin=True)
    assert result is not None
    assert result.id == problem.id


# ── search / filter ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_by_title(session: AsyncSession) -> None:
    author = await _make_user(session)
    await _make_problem(session, author.id, title="Fibonacci Sequence")
    await _make_problem(session, author.id, title="Prime Sieve")
    await session.flush()

    pagination = await admin_problem_service.list_problems_paginated(
        session,
        page=1,
        per_page=25,
        search="fibonacci",
        caller_id=author.id,
        is_admin=False,
    )
    assert pagination.total == 1
    assert pagination.items[0].title == "Fibonacci Sequence"


@pytest.mark.asyncio
async def test_search_resolves_owner_and_free_text_authors(session: AsyncSession) -> None:
    owner = await _make_user(session, email_suffix="search-owner")
    owner.nome = "Ada Lovelace"
    owner_problem = await _make_problem(session, owner.id, title="Owner-authored")
    external_problem = await admin_problem_service.create_problem(
        session,
        caller_id=owner.id,
        title="External-authored",
        author="Grace Hopper",
        author_is_owner=False,
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
        validator_type=ProblemValidatorType.STANDARD,
    )
    await session.flush()

    owner_search = await admin_problem_service.list_problems_paginated(
        session,
        page=1,
        per_page=25,
        search="Ada Lovelace",
        caller_id=owner.id,
        is_admin=False,
    )
    external_search = await admin_problem_service.list_problems_paginated(
        session,
        page=1,
        per_page=25,
        search="Grace Hopper",
        caller_id=owner.id,
        is_admin=False,
    )

    assert [item.id for item in owner_search.items] == [owner_problem.id]
    assert [item.id for item in external_search.items] == [external_problem.id]


@pytest.mark.asyncio
async def test_problem_list_item_marks_custom_validator_problems(session: AsyncSession) -> None:
    author = await _make_user(session)
    language = await _make_language(session)
    plain_problem = await _make_problem(session, author.id, title="Plain")
    validator_problem = await _make_problem(
        session, author.id, title="Interactive", validator_type=ProblemValidatorType.INTERACTIVE
    )
    candidate_problem = await _make_problem(
        session, author.id, title="Candidate", validator_type=ProblemValidatorType.INTERACTIVE
    )
    session.add(
        ArenaProblemCustomValidator(
            problem_id=validator_problem.id,
            active_language_id=language.id,
            active_source="print('validator')\n",
            active_state=CustomValidatorActiveState.VALID,
            active_validated_at=datetime.now(UTC),
        )
    )
    session.add(
        ArenaProblemCustomValidator(
            problem_id=candidate_problem.id,
            candidate_language_id=language.id,
            candidate_source="print('candidate')\n",
            candidate_token=str(uuid.uuid4()),
            candidate_state=CustomValidatorCandidateState.PENDING,
        )
    )
    await session.flush()

    pagination = await admin_problem_service.list_problems_paginated(
        session,
        page=1,
        per_page=25,
        caller_id=author.id,
        is_admin=False,
    )

    flags_by_problem = {item.id: item.has_custom_validator for item in pagination.items}
    assert flags_by_problem[plain_problem.id] is False
    assert flags_by_problem[validator_problem.id] is True
    assert flags_by_problem[candidate_problem.id] is True


@pytest.mark.asyncio
async def test_problem_list_uses_page_scoped_enrichment_queries(
    session: AsyncSession,
    sql_statements: list[str],
) -> None:
    """The admin list keeps counts cheap and enriches only the current page."""
    author = await _make_user(session)
    problem = await _make_problem(session, author.id, title="Narrow projection")
    problem.problem_image_base64 = "large-image-payload"
    problem.problem_image_mime = "image/png"
    problem.problem_image_caption = "Caption"
    problem.notes = "Internal note"
    problem.license = "CC BY 4.0"
    session.add_all(
        [
            ArenaTestCase(problem_id=problem.id, ordinal=1, is_sample=True),
            ArenaTestCase(problem_id=problem.id, ordinal=2, is_sample=False),
            ArenaTestCase(problem_id=problem.id, ordinal=3, is_sample=False),
        ]
    )
    await session.flush()

    sql_statements.clear()
    pagination = await admin_problem_service.list_problems_paginated(
        session,
        page=1,
        per_page=25,
        caller_id=author.id,
        is_admin=False,
    )

    # Four, not five: the interactive marker now comes from the page query's own
    # validator_type column instead of a separate validator lookup.
    assert len(sql_statements) == 4
    count_sql, page_sql, category_sql, test_case_sql = [statement.lower() for statement in sql_statements]
    assert "arena_problem_ratings" not in count_sql
    assert "arena_test_cases" not in count_sql
    assert "arena_problem_custom_validators" not in count_sql
    for large_column in (
        "problem_statement",
        "problem_image_base64",
        "problem_image_caption",
        "notes",
        "license",
    ):
        assert large_column not in page_sql
    assert " in (" in category_sql
    assert " in (" in test_case_sql

    item = pagination.items[0]
    assert item.difficulty.value is None
    assert item.public_tc_count == 1
    assert item.private_tc_count == 2


async def _make_or_filter_fixture(session: AsyncSession) -> tuple[ArenaUser, ArenaCategory, ArenaCategory]:
    """Create problems that exercise every OR-filter matching state."""
    author = await _make_user(session)
    cat_a = ArenaCategory(name="Graphs", slug="graphs", color="#ff0000")
    cat_b = ArenaCategory(name="DP", slug="dp", color="#00ff00")
    session.add_all([cat_a, cat_b])
    await session.flush()

    for title, category_ids in (
        ("Both", [cat_a.id, cat_b.id]),
        ("One", [cat_a.id]),
        ("Two", [cat_b.id]),
        ("Uncategorized", []),
    ):
        await admin_problem_service.create_problem(
            session,
            caller_id=author.id,
            title=title,
            source=None,
            hide_author_show_source=False,
            time_limit_ms=1000,
            memory_limit_kb=262144,
            pids_limit=64,
            output_limit_in_bytes=65536,
            problem_statement="Hello world",
            image_b64=None,
            image_mime=None,
            image_caption=None,
            notes=None,
            category_ids=category_ids,
            validator_type=ProblemValidatorType.STANDARD,
        )
    await session.flush()
    return author, cat_a, cat_b


def _assert_or_filter_results(pagination: object) -> None:
    """Assert the shared OR-filter fixture returns one row per matching problem."""
    assert pagination.total == 3
    assert [item.title for item in pagination.items] == ["Both", "One", "Two"]
    assert len({item.id for item in pagination.items}) == 3


@pytest.mark.asyncio
async def test_category_id_filter_uses_or_semantics(session: AsyncSession) -> None:
    """Selecting multiple category IDs returns problems linked to any of them."""
    author, cat_a, cat_b = await _make_or_filter_fixture(session)

    pagination = await admin_problem_service.list_problems_paginated(
        session,
        page=1,
        per_page=25,
        category_ids=[cat_a.id, cat_b.id],
        caller_id=author.id,
        is_admin=False,
    )

    _assert_or_filter_results(pagination)


@pytest.mark.asyncio
async def test_category_slug_filter_uses_or_semantics(session: AsyncSession) -> None:
    """Selecting multiple category slugs returns problems linked to any of them."""
    author, cat_a, cat_b = await _make_or_filter_fixture(session)

    pagination = await admin_problem_service.list_problems_paginated(
        session,
        page=1,
        per_page=25,
        category_slugs=[cat_a.slug, cat_b.slug],
        caller_id=author.id,
        is_admin=False,
    )

    _assert_or_filter_results(pagination)


@pytest.mark.asyncio
async def test_enabled_filter(session: AsyncSession) -> None:
    author = await _make_user(session)
    enabled_problem = await admin_problem_service.create_problem(
        session,
        caller_id=author.id,
        title="Enabled Problem",
        validator_type=ProblemValidatorType.STANDARD,
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="Hello world",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[],
    )
    await admin_problem_service.create_problem(
        session,
        caller_id=author.id,
        title="Disabled Problem",
        validator_type=ProblemValidatorType.STANDARD,
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="Hello world",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[],
    )
    enabled_problem.enabled = True
    await session.flush()

    pagination = await admin_problem_service.list_problems_paginated(
        session,
        page=1,
        per_page=25,
        enabled=True,
        caller_id=author.id,
        is_admin=True,
    )
    assert [item.title for item in pagination.items] == ["Enabled Problem"]

    pagination = await admin_problem_service.list_problems_paginated(
        session,
        page=1,
        per_page=25,
        enabled=False,
        caller_id=author.id,
        is_admin=True,
    )
    assert [item.title for item in pagination.items] == ["Disabled Problem"]

    pagination = await admin_problem_service.list_problems_paginated(
        session,
        page=1,
        per_page=25,
        caller_id=author.id,
        is_admin=True,
    )
    titles = {item.title for item in pagination.items}
    assert titles == {"Enabled Problem", "Disabled Problem"}


@pytest.mark.asyncio
async def test_editorial_filter(session: AsyncSession) -> None:
    author = await _make_user(session)

    async def _create(title: str, *, editorial: str | None, policy: ArenaEditorialReleasePolicy) -> ArenaProblem:
        return await admin_problem_service.create_problem(
            session,
            caller_id=author.id,
            title=title,
            validator_type=ProblemValidatorType.STANDARD,
            source=None,
            hide_author_show_source=False,
            time_limit_ms=1000,
            memory_limit_kb=262144,
            pids_limit=64,
            output_limit_in_bytes=65536,
            problem_statement="Hello world",
            image_b64=None,
            image_mime=None,
            image_caption=None,
            notes=None,
            category_ids=[],
            editorial=editorial,
            editorial_release_policy=policy,
        )

    no_editorial = await _create("No Editorial", editorial=None, policy=ArenaEditorialReleasePolicy.NEVER)
    never_problem = await _create(
        "Never Editorial", editorial="Solution guide", policy=ArenaEditorialReleasePolicy.NEVER
    )
    always_problem = await _create(
        "Always Editorial", editorial="Solution guide", policy=ArenaEditorialReleasePolicy.ALWAYS
    )
    after_ac_problem = await _create(
        "After AC Editorial", editorial="Solution guide", policy=ArenaEditorialReleasePolicy.AFTER_AC
    )
    await session.flush()

    async def _titles(editorial_filter: str) -> set[str]:
        pagination = await admin_problem_service.list_problems_paginated(
            session,
            page=1,
            per_page=25,
            editorial=editorial_filter,
            caller_id=author.id,
            is_admin=True,
        )
        return {item.title for item in pagination.items}

    assert await _titles("none") == {no_editorial.title}
    assert await _titles("never") == {never_problem.title}
    assert await _titles("always") == {always_problem.title}
    assert await _titles("after_ac") == {after_ac_problem.title}

    pagination = await admin_problem_service.list_problems_paginated(
        session,
        page=1,
        per_page=25,
        caller_id=author.id,
        is_admin=True,
    )
    items_by_title = {item.title: item for item in pagination.items}
    assert items_by_title[no_editorial.title].has_editorial is False
    assert items_by_title[never_problem.title].has_editorial is True
    assert items_by_title[never_problem.title].editorial_release_policy == ArenaEditorialReleasePolicy.NEVER
    assert items_by_title[always_problem.title].editorial_release_policy == ArenaEditorialReleasePolicy.ALWAYS
    assert items_by_title[after_ac_problem.title].editorial_release_policy == ArenaEditorialReleasePolicy.AFTER_AC


# ── list_owners ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_owners_returns_admin_and_can_edit_users(session: AsyncSession) -> None:
    admin = await _make_user(session, role=ArenaRole.ARENA_ADMIN, email_suffix="la-admin")
    judge_with_edit = await _make_user(session, role=ArenaRole.ARENA_JUDGE, can_edit=True, email_suffix="la-judge-edit")
    judge_no_edit = await _make_user(
        session, role=ArenaRole.ARENA_JUDGE, can_edit=False, email_suffix="la-judge-noedit"
    )
    user_with_edit = await _make_user(session, role=ArenaRole.ARENA_USER, can_edit=True, email_suffix="la-user-edit")
    user_no_edit = await _make_user(session, role=ArenaRole.ARENA_USER, can_edit=False, email_suffix="la-user-noedit")

    owners = await admin_problem_service.list_owners(session)
    owner_ids = {user.id for user in owners}

    assert admin.id in owner_ids
    assert judge_with_edit.id in owner_ids
    assert user_with_edit.id in owner_ids
    assert judge_no_edit.id not in owner_ids
    assert user_no_edit.id not in owner_ids


# ── category search ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_categories(session: AsyncSession) -> None:
    session.add_all(
        [
            ArenaCategory(name="Binary Search", slug="binary-search", color="#aabbcc"),
            ArenaCategory(name="BFS", slug="bfs", color="#ddeeff"),
            ArenaCategory(name="DFS", slug="dfs", color="#112233"),
        ]
    )
    await session.flush()

    results = await admin_problem_service.search_categories(session, query="b")
    names = [c.name for c in results]
    assert "BFS" in names
    assert "Binary Search" in names
    assert "DFS" not in names


# ── update_problem: image ─────────────────────────────────────────────────────


async def _update_image(
    session: AsyncSession,
    problem: ArenaProblem,
    *,
    image_b64: str | None,
    image_mime: str | None,
    image_caption: str | None,
    clear_image: bool,
) -> ArenaProblem:
    updated = await admin_problem_service.update_problem(
        session,
        problem,
        title=problem.title,
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="Hello world",
        image_b64=image_b64,
        image_mime=image_mime,
        image_caption=image_caption,
        notes=None,
        clear_image=clear_image,
        category_ids=[],
    )
    await session.flush()
    return updated


@pytest.mark.asyncio
async def test_update_problem_clear_image_also_clears_caption(session: AsyncSession) -> None:
    """Removing the image removes its caption, even when one is still submitted.

    The caption input stays populated in the form when the remove checkbox is
    ticked, so the service must not keep a caption with no image to caption.
    """
    owner = await _make_user(session)
    problem = await _make_problem(session, owner.id)
    await _update_image(
        session,
        problem,
        image_b64="AAAA",
        image_mime="image/png",
        image_caption="A red square",
        clear_image=False,
    )
    assert problem.problem_image_caption == "A red square"

    await _update_image(
        session,
        problem,
        image_b64=None,
        image_mime=None,
        image_caption="A red square",
        clear_image=True,
    )

    assert problem.problem_image_base64 is None
    assert problem.problem_image_mime is None
    assert problem.problem_image_caption is None


@pytest.mark.asyncio
async def test_update_problem_replacement_wins_over_clear_image(session: AsyncSession) -> None:
    owner = await _make_user(session)
    problem = await _make_problem(session, owner.id)
    await _update_image(
        session,
        problem,
        image_b64="AAAA",
        image_mime="image/png",
        image_caption="Old",
        clear_image=False,
    )

    await _update_image(
        session,
        problem,
        image_b64="BBBB",
        image_mime="image/webp",
        image_caption="New",
        clear_image=True,
    )

    assert problem.problem_image_base64 == "BBBB"
    assert problem.problem_image_mime == "image/webp"
    assert problem.problem_image_caption == "New"
