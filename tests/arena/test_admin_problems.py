#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for Arena admin problem management."""

from datetime import UTC, datetime

import pytest
from _admin_problem_app import (
    build_admin_app as _build_admin_app,
)
from _admin_problem_app import (
    create_language as _create_language,
)
from _admin_problem_app import (
    create_user as _create_user,
)
from _admin_problem_app import (
    login_token as _login_token,
)
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.config import settings as arena_settings
from arena.models.arena_problems import ArenaCategory, ArenaProblemCustomValidator
from arena.services import admin_problem_service, admin_problem_tc_service
from shared.enumerations import (
    ArenaEditorialReleasePolicy,
    ArenaRole,
    CustomValidatorActiveState,
    ProblemValidatorType,
)

# ── Authorization tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_problem_list_requires_login(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/admin/problems")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_problem_list_denies_arena_user(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    user = await _create_user(session, email="u1@test.example", role=ArenaRole.ARENA_USER)
    token = _login_token(app, user)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/problems")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_problem_list_denies_judge_without_can_edit(session: AsyncSession) -> None:
    """A judge without the can_edit grant may not manage the problem base."""
    app = _build_admin_app(session)
    judge = await _create_user(session, email="j1@test.example", role=ArenaRole.ARENA_JUDGE)
    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/problems")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_problem_list_allows_judge_with_can_edit(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(session, email="j1edit@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/problems")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_problem_list_allows_user_with_can_edit(session: AsyncSession) -> None:
    """The can_edit grant authorizes problem management regardless of role."""
    app = _build_admin_app(session)
    user = await _create_user(session, email="uedit@test.example", role=ArenaRole.ARENA_USER, can_edit=True)
    token = _login_token(app, user)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/problems")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_problem_list_allows_admin(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_user(session, email="a1@test.example", role=ArenaRole.ARENA_ADMIN)
    token = _login_token(app, admin)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/problems")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_problem_search_defaults_to_relevance_and_offers_relevance_reset(
    session: AsyncSession,
) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(
        session,
        email="jsearch@test.example",
        role=ArenaRole.ARENA_JUDGE,
        can_edit=True,
    )
    await admin_problem_service.create_problem(
        session,
        caller_id=judge.id,
        title="Searchable Graph",
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
    await session.commit()
    token = _login_token(app, judge)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        default_response = await client.get("/admin/problems", params={"search": "Graph"})
        title_response = await client.get(
            "/admin/problems",
            params={"search": "Graph", "sort_by": "title_asc"},
        )

    assert default_response.status_code == 200
    assert "Sorted by relevance" in default_response.text
    assert title_response.status_code == 200
    assert "Sort by relevance" in title_response.text
    assert "sort_by=relevance" in title_response.text


@pytest.mark.asyncio
async def test_problem_list_filters_by_enabled_status(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(
        session,
        email="jenabled@test.example",
        role=ArenaRole.ARENA_JUDGE,
        can_edit=True,
    )
    enabled_problem = await admin_problem_service.create_problem(
        session,
        caller_id=judge.id,
        title="Visible Problem",
        validator_type=ProblemValidatorType.STANDARD,
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
    await admin_problem_service.create_problem(
        session,
        caller_id=judge.id,
        title="Hidden Problem",
        validator_type=ProblemValidatorType.STANDARD,
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
    enabled_problem.enabled = True
    await session.commit()
    token = _login_token(app, judge)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        enabled_response = await client.get("/admin/problems", params={"enabled": "1"})
        disabled_response = await client.get("/admin/problems", params={"enabled": "0"})
        all_response = await client.get("/admin/problems")

    assert "Visible Problem" in enabled_response.text
    assert "Hidden Problem" not in enabled_response.text

    assert "Hidden Problem" in disabled_response.text
    assert "Visible Problem" not in disabled_response.text

    assert "Visible Problem" in all_response.text
    assert "Hidden Problem" in all_response.text


@pytest.mark.asyncio
async def test_problem_list_filters_by_editorial(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(
        session,
        email="jeditorial@test.example",
        role=ArenaRole.ARENA_JUDGE,
        can_edit=True,
    )

    async def _create(title: str, *, editorial: str | None, policy: ArenaEditorialReleasePolicy) -> None:
        await admin_problem_service.create_problem(
            session,
            caller_id=judge.id,
            title=title,
            validator_type=ProblemValidatorType.STANDARD,
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
            editorial=editorial,
            editorial_release_policy=policy,
        )

    await _create("No Editorial Item", editorial=None, policy=ArenaEditorialReleasePolicy.NEVER)
    await _create("Never Editorial Item", editorial="Solution", policy=ArenaEditorialReleasePolicy.NEVER)
    await _create("Always Editorial Item", editorial="Solution", policy=ArenaEditorialReleasePolicy.ALWAYS)
    await _create("After AC Editorial Item", editorial="Solution", policy=ArenaEditorialReleasePolicy.AFTER_AC)
    await session.commit()
    token = _login_token(app, judge)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        none_response = await client.get("/admin/problems", params={"editorial": "none"})
        never_response = await client.get("/admin/problems", params={"editorial": "never"})
        always_response = await client.get("/admin/problems", params={"editorial": "always"})
        after_ac_response = await client.get("/admin/problems", params={"editorial": "after_ac"})
        bogus_response = await client.get("/admin/problems", params={"editorial": "klingon"})

    assert "No Editorial Item" in none_response.text
    assert "Never Editorial Item" not in none_response.text
    assert "Always Editorial Item" not in none_response.text
    assert "After AC Editorial Item" not in none_response.text

    assert "Never Editorial Item" in never_response.text
    assert "No Editorial Item" not in never_response.text

    assert "Always Editorial Item" in always_response.text
    assert "No Editorial Item" not in always_response.text

    assert "After AC Editorial Item" in after_ac_response.text
    assert "No Editorial Item" not in after_ac_response.text

    for title in (
        "No Editorial Item",
        "Never Editorial Item",
        "Always Editorial Item",
        "After AC Editorial Item",
    ):
        assert title in bogus_response.text


@pytest.mark.asyncio
async def test_problem_list_renders_custom_validator_marker(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(
        session,
        email="jvalidator@test.example",
        role=ArenaRole.ARENA_JUDGE,
        can_edit=True,
    )
    language = await _create_language(session)
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=judge.id,
        title="Interactive Admin Problem",
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="stmt",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[],
        validator_type=ProblemValidatorType.INTERACTIVE,
    )
    session.add(
        ArenaProblemCustomValidator(
            problem_id=problem.id,
            active_language_id=language.id,
            active_source="print('validator')\n",
            active_state=CustomValidatorActiveState.VALID,
            active_validated_at=datetime.now(UTC),
        )
    )
    await session.commit()

    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/problems")

    assert response.status_code == 200
    assert "Interactive Admin Problem" in response.text
    assert "published_with_changes" in response.text
    assert "This problem uses a custom validator" in response.text


@pytest.mark.asyncio
async def test_problem_list_filters_categories_by_slug(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(session, email="jcat@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    category = ArenaCategory(name="Graphs", slug="graphs", color="#ff0000")
    session.add(category)
    await session.flush()
    await admin_problem_service.create_problem(
        session,
        caller_id=judge.id,
        title="Graph Paths",
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="stmt",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[category.id],
        validator_type=ProblemValidatorType.STANDARD,
    )
    await admin_problem_service.create_problem(
        session,
        caller_id=judge.id,
        title="Plain Math",
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="stmt",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[],
        validator_type=ProblemValidatorType.STANDARD,
    )
    await session.commit()

    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/problems?category_slugs=graphs")

    assert response.status_code == 200
    assert "Graph Paths" in response.text
    assert "Plain Math" not in response.text
    assert 'name="category_slugs"' in response.text
    assert 'value="graphs"' in response.text


# ── Create problem ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_problem_create_renders_form(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(session, email="j2@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/problems/new/standard")
    assert response.status_code == 200
    assert "New Problem" in response.text
    assert 'id="author_is_owner"' in response.text
    assert 'id="author-field"' in response.text
    assert "GIF, JPEG, PNG, or WebP" in response.text
    assert 'accept=".gif,.png,.jpg,.jpeg,.webp"' in response.text
    assert "up to 2.0 MiB" in response.text
    assert "up to 2048 × 2048 px" in response.text
    author_field_attributes = response.text.split('id="author-field"', 1)[1].split(">", 1)[0]
    assert "hidden" in author_field_attributes


@pytest.mark.asyncio
async def test_problem_create_success_lands_on_the_judgment_pages(session: AsyncSession) -> None:
    """Creation collects the definition, then hands the author the next job.

    It used to return to the problem list, which said nothing about the problem
    being unjudgeable until it has test cases. A standard problem lands on its
    test cases; an interactive one lands on its validator page (covered by
    ``tests/arena/test_admin_problem_chooser.py``).
    """
    app = _build_admin_app(session)
    judge = await _create_user(session, email="j3@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.post(
            "/admin/problems/new/standard",
            data={
                "title": "My First Problem",
                "author_is_owner": "true",
                "source": "",
                "time_limit_ms": "1000",
                "memory_limit_kb": "262144",
                "pids_limit": "64",
                "output_limit_in_bytes": "65536",
                "problem_statement": "Hello",
            },
        )
    assert response.status_code == 303
    assert response.headers["location"].endswith("/judgment/test-cases")


@pytest.mark.asyncio
async def test_problem_create_blank_title_returns_400(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(session, email="j4@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            "/admin/problems/new/standard",
            data={
                "title": "   ",
                "author_is_owner": "true",
                "time_limit_ms": "1000",
                "memory_limit_kb": "262144",
                "pids_limit": "64",
                "output_limit_in_bytes": "65536",
                "problem_statement": "x",
            },
        )
    assert response.status_code == 422


# ── Edit problem ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_problem_edit_shows_owner_link_to_admin(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_user(
        session,
        name="Arena Admin",
        email="admin-problem-author@test.example",
        role=ArenaRole.ARENA_ADMIN,
    )
    author = await _create_user(
        session,
        name="Problem Author",
        email="problem-author@test.example",
        role=ArenaRole.ARENA_JUDGE,
        can_edit=True,
    )
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=author.id,
        title="Authored Problem",
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="stmt",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[],
        validator_type=ProblemValidatorType.STANDARD,
    )
    await session.commit()

    token = _login_token(app, admin)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.get(f"/admin/problems/{problem.id}/edit")

    assert response.status_code == 200
    assert "Problem Author" in response.text
    assert "Difficulty" in response.text
    assert f"/admin/users/{author.id}" in response.text
    assert "GIF, JPEG, PNG, or WebP" in response.text
    assert 'accept=".gif,.png,.jpg,.jpeg,.webp"' in response.text


@pytest.mark.asyncio
async def test_problem_edit_hides_owner_link_from_judge(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(
        session,
        name="Judge Author",
        email="judge-problem-author@test.example",
        role=ArenaRole.ARENA_JUDGE,
        can_edit=True,
    )
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=judge.id,
        title="Judge Problem",
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="stmt",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[],
        validator_type=ProblemValidatorType.STANDARD,
    )
    await session.commit()

    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.get(f"/admin/problems/{problem.id}/edit")

    assert response.status_code == 200
    assert f"/admin/users/{judge.id}" not in response.text


# ── Judge isolation ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_judge_cannot_edit_other_judges_problem(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge_a = await _create_user(session, email="ja@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    judge_b = await _create_user(session, email="jb@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    # Create a problem as judge_a
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=judge_a.id,
        title="Judge A Problem",
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="stmt",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[],
        validator_type=ProblemValidatorType.STANDARD,
    )
    await session.commit()

    # Try to edit as judge_b
    token_b = _login_token(app, judge_b)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token_b}
    ) as client:
        response = await client.get(
            f"/admin/problems/{problem.id}/edit",
        )
    assert response.status_code == 404


# ── Update problem ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_problem_update_redirects_to_list(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(session, email="j7@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=judge.id,
        title="Original Problem",
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="stmt",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[],
        validator_type=ProblemValidatorType.STANDARD,
    )
    await session.commit()

    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.post(
            f"/admin/problems/{problem.id}/edit",
            data={
                "title": "Updated Problem",
                "author": "External Writer",
                "license": "CC0 1.0",
                "source": "",
                "time_limit_ms": "1000",
                "memory_limit_kb": "262144",
                "pids_limit": "64",
                "output_limit_in_bytes": "65536",
                "problem_statement": "updated stmt",
                "save_action": "disable",
                "return_page": "3",
                "return_per_page": "50",
                "return_search": "graphs",
                "return_sort_by": "rating_desc",
                "return_owner_id": judge.id,
                "return_category_slugs": ["graphs", "dynamic-programming"],
            },
        )

    assert response.status_code == 303
    assert (
        response.headers["location"]
        == "http://testserver/admin/problems?page=3&per_page=50&search=graphs&sort_by=rating_desc"
        f"&owner_id={judge.id}&category_slugs=graphs&category_slugs=dynamic-programming#{problem.id}"
    )
    await session.refresh(problem)
    assert problem.owner_id == judge.id
    assert problem.author == "External Writer"
    assert problem.author_is_owner is False
    assert problem.license == "CC0 1.0"


# ── Toggle enabled ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_toggle_enabled_redirects_to_list(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(session, email="j5@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=judge.id,
        title="Toggle Problem",
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="stmt",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[],
        validator_type=ProblemValidatorType.STANDARD,
    )
    # Every problem needs a test case before it can judge, so before it can be enabled.
    _tc, write_files = await admin_problem_tc_service.create_testcase(
        session,
        problem,
        input_content="1",
        output_content="1",
        is_sample=True,
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )
    await session.commit()
    write_files()

    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.post(
            f"/admin/problems/{problem.id}/toggle-enabled?search=toggle&per_page=50&page=2",
        )
    assert response.status_code == 303
    assert response.headers["location"] == (
        f"http://testserver/admin/problems?page=2&per_page=50&search=toggle#{problem.id}"
    )

    # Verify flag changed in DB
    await session.refresh(problem)
    assert problem.enabled is True


@pytest.mark.asyncio
async def test_toggle_enabled_is_refused_without_a_test_case(session: AsyncSession) -> None:
    """A problem with no test case cannot be judged, so it cannot be enabled."""
    app = _build_admin_app(session)
    judge = await _create_user(session, email="j6@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=judge.id,
        title="Empty Problem",
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="stmt",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[],
        validator_type=ProblemValidatorType.STANDARD,
    )
    await session.commit()

    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.post(f"/admin/problems/{problem.id}/toggle-enabled")

    assert response.status_code == 303
    assert response.headers["location"].endswith(f"/admin/problems/{problem.id}/edit")
    await session.refresh(problem)
    assert problem.enabled is False


@pytest.mark.asyncio
async def test_toggle_enabled_is_refused_after_removing_the_validator(
    session: AsyncSession,
) -> None:
    """An interactive problem stripped of its validator cannot be enabled.

    It also does not become a plain problem: the strategy is immutable, so the
    problem stays interactive and its output-less cases stay exactly what they
    were meant to be. Enabling is blocked until a validator compiles again,
    rather than the problem silently becoming token-compared against cases that
    have nothing to compare.
    """
    app = _build_admin_app(session)
    judge = await _create_user(session, email="j7@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    language = await _create_language(session)
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=judge.id,
        title="Formerly Interactive Problem",
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="stmt",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[],
        validator_type=ProblemValidatorType.INTERACTIVE,
    )
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
    _tc, write_files = await admin_problem_tc_service.create_testcase(
        session,
        problem,
        input_content="7\n",
        output_content="ignored while interactive",
        is_sample=True,
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )
    await session.commit()
    write_files()

    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": token},
    ) as client:
        remove_response = await client.post(
            f"/admin/problems/{problem.id}/validator/remove",
            data={"keep_interactions": "true"},
        )
        assert remove_response.status_code == 303

        enable_response = await client.post(f"/admin/problems/{problem.id}/toggle-enabled")

    assert enable_response.status_code == 303
    assert enable_response.headers["location"].endswith(f"/admin/problems/{problem.id}/edit")
    await session.refresh(problem)
    assert problem.enabled is False
    assert problem.validator_type is ProblemValidatorType.INTERACTIVE
    assert await session.get(ArenaProblemCustomValidator, problem.id) is None


# ── Toggle test-case sample/secret ────────────────────────────────────────────


async def _make_problem_with_tc(session: AsyncSession, owner_id: str, *, is_sample: bool = False) -> tuple[str, str]:
    """Create a problem with one test case; return (problem_id, tc_id)."""
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=owner_id,
        title="TC Toggle Problem",
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="stmt",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[],
        validator_type=ProblemValidatorType.STANDARD,
    )
    tc, write_files = await admin_problem_tc_service.create_testcase(
        session,
        problem,
        input_content="1",
        output_content="1",
        is_sample=is_sample,
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )
    await session.commit()
    write_files()
    return problem.id, tc.id


@pytest.mark.asyncio
async def test_judgment_page_renders_toggle_button_and_guard_scripts(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(session, email="tctoggle-render@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem_id, tc_id = await _make_problem_with_tc(session, judge.id)

    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get(f"/admin/problems/{problem_id}/judgment/test-cases")

    assert response.status_code == 200
    body = response.text
    # Row anchor for the highlight pattern
    assert f'id="tc-{tc_id}"' in body
    # The toggle posts to the judgment endpoint and applies immediately; the
    # editor's own pane no longer carries pending state for it.
    assert f"/judgment/test-cases/{tc_id}/toggle-sample" in body
    assert "swap_horiz" in body
    # UI warning + row highlight assets are wired on the page that owns the rows
    assert "problem-edit-unsaved-guard.js" in body
    assert "highlight-row.js" in body
    # The row keeps one obvious Edit action and discloses the rest behind More,
    # which replaced the older icon btn-group (see the Contest-side counterpart
    # in tests/web/test_contest_admin_problem_tc.py).
    assert "noca-row-actions" in body
    assert "dropdown-menu dropdown-menu-end" in body
    assert "More actions for test case" in body
    assert "Remove test case" in body
    assert "noca-icon-btn-group" not in body


@pytest.mark.asyncio
async def test_toggle_sample_flips_and_redirects_to_row_anchor(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(session, email="tctoggle-post@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem_id, tc_id = await _make_problem_with_tc(session, judge.id, is_sample=False)

    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.post(f"/admin/problems/{problem_id}/judgment/test-cases/{tc_id}/toggle-sample")

    assert response.status_code == 303
    # The action returns to the page that owns it, and to the row it changed.
    assert response.headers["location"] == (
        f"http://testserver/admin/problems/{problem_id}/judgment/test-cases#tc-{tc_id}"
    )

    tc = await admin_problem_tc_service.get_testcase(session, tc_id, problem_id=problem_id)
    assert tc is not None
    assert tc.is_sample is True


@pytest.mark.asyncio
async def test_judge_cannot_toggle_other_judges_testcase(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge_a = await _create_user(session, email="tctoggle-a@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    judge_b = await _create_user(session, email="tctoggle-b@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem_id, tc_id = await _make_problem_with_tc(session, judge_a.id, is_sample=False)

    token_b = _login_token(app, judge_b)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": token_b},
    ) as client:
        response = await client.post(f"/admin/problems/{problem_id}/judgment/test-cases/{tc_id}/toggle-sample")

    assert response.status_code == 404
    # Flag must remain unchanged
    tc = await admin_problem_tc_service.get_testcase(session, tc_id, problem_id=problem_id)
    assert tc is not None
    assert tc.is_sample is False


# ── Admin sees all problems ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_list_shows_all_problems(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(session, email="j6@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    admin = await _create_user(session, email="adm2@test.example", role=ArenaRole.ARENA_ADMIN)

    problem = await admin_problem_service.create_problem(
        session,
        caller_id=judge.id,
        title="Visible to Admin",
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="stmt",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[],
        validator_type=ProblemValidatorType.STANDARD,
    )
    await session.commit()

    token_admin = _login_token(app, admin)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token_admin}
    ) as client:
        response = await client.get("/admin/problems")
    assert response.status_code == 200
    assert "Visible to Admin" in response.text
    assert "Difficulty" in response.text
    assert f'id="{problem.id}"' in response.text
    assert "highlight-row.js" in response.text
