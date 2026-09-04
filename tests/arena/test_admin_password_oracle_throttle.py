#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The admin password-confirmation fields are not unthrottled oracles.

Issue #144 put Arena's secret-verifying routes behind the shared
``password_verify`` bucket, but the sweep missed every password confirmation in
``admin_users_actions`` (seven routes), the problem delete, and the problem-set
delete. Each re-checked the acting user's own password with no lockout, so any
of them could be guessed against indefinitely -- and rotating between them would
have multiplied a per-route budget had one existed.

Arena route tests run without Valkey, so the shared limiter answers from its
process-local fallback, which ``tests/arena/conftest.py`` clears around every
test.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_problems import ArenaProblem
from arena.models.arena_users import ArenaUser
from shared.enumerations import ArenaRole
from tests.arena.test_admin_problem_judgment import _client as _judge_client
from tests.arena.test_admin_problem_judgment import _make_problem
from tests.arena.test_admin_users import (
    _TEST_PASSWORD,
    _build_admin_app,
    _create_arena_user,
    _login_token,
)

_LIMIT = 2
_LOCKOUT = 900
_WRONG = "WrongPass1!"


def _configure(monkeypatch: pytest.MonkeyPatch, *, ip_max: int = 100) -> None:
    """Pin the shared auth-throttle knobs the routes read at request time."""
    target = "arena.routes.auth_common.settings"
    monkeypatch.setattr(f"{target}.AUTH_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(f"{target}.AUTH_RATE_LIMIT_WINDOW_SECONDS", 900)
    monkeypatch.setattr(f"{target}.AUTH_RATE_LIMIT_ACCOUNT_MAX_FAILURES", _LIMIT)
    monkeypatch.setattr(f"{target}.AUTH_RATE_LIMIT_IP_MAX_FAILURES", ip_max)
    monkeypatch.setattr(f"{target}.AUTH_RATE_LIMIT_LOCKOUT_SECONDS", _LOCKOUT)


def _assert_locked(response: object) -> None:
    """The lockout page, not a redirect back to the form."""
    assert response.status_code == 429  # type: ignore[attr-defined]
    assert "Too many" in response.text  # type: ignore[attr-defined]


async def _admin_and_target(session: AsyncSession) -> tuple[ArenaUser, ArenaUser]:
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    return admin, target


# Every admin action that confirms the acting admin's password, with a payload
# that is otherwise valid, so only the password decides the outcome.
_ADMIN_ACTIONS = [
    ("role", {"new_role": ArenaRole.ARENA_JUDGE.value}),
    ("toggle-active", {}),
    ("force-password-change", {}),
    ("toggle-can-edit", {}),
    ("toggle-ranking-visible", {}),
    ("toggle-public-profile", {}),
    ("disable-2fa", {}),
    ("unlink-google", {}),
]


@pytest.mark.parametrize(("action", "extra"), _ADMIN_ACTIONS)
@pytest.mark.asyncio
async def test_admin_user_action_locks_out_after_wrong_passwords(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, action: str, extra: dict[str, str]
) -> None:
    """Each admin-user action counts wrong passwords and then refuses the right one."""
    _configure(monkeypatch)
    app = _build_admin_app(session)
    admin, target = await _admin_and_target(session)
    token = _login_token(app, admin)
    url = f"/admin/users/{target.id}/{action}"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        for _ in range(_LIMIT):
            wrong = await client.post(url, data={**extra, "confirm_password": _WRONG}, follow_redirects=False)
            assert wrong.status_code == 303, "a wrong password redirects back, it does not lock yet"

        locked = await client.post(url, data={**extra, "confirm_password": _WRONG}, follow_redirects=False)
        _assert_locked(locked)

        # The lockout is checked before the hash, so the correct password is
        # refused too and the action does not run.
        correct = await client.post(url, data={**extra, "confirm_password": _TEST_PASSWORD}, follow_redirects=False)
        _assert_locked(correct)


@pytest.mark.asyncio
async def test_admin_actions_share_one_password_budget(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rotating between the admin actions cannot multiply the guess budget."""
    _configure(monkeypatch)
    app = _build_admin_app(session)
    admin, target = await _admin_and_target(session)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        first = await client.post(
            f"/admin/users/{target.id}/toggle-active",
            data={"confirm_password": _WRONG},
            follow_redirects=False,
        )
        assert first.status_code == 303
        second = await client.post(
            f"/admin/users/{target.id}/toggle-can-edit",
            data={"confirm_password": _WRONG},
            follow_redirects=False,
        )
        assert second.status_code == 303

        # Two failures spent across two different routes; the third is refused
        # on a third route, because all of them share password_verify.
        third = await client.post(
            f"/admin/users/{target.id}/toggle-public-profile",
            data={"confirm_password": _WRONG},
            follow_redirects=False,
        )
        _assert_locked(third)


@pytest.mark.asyncio
async def test_a_correct_password_resets_the_admin_budget(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful confirmation clears the counter, as the other buckets do."""
    _configure(monkeypatch)
    app = _build_admin_app(session)
    admin, target = await _admin_and_target(session)
    token = _login_token(app, admin)
    url = f"/admin/users/{target.id}/toggle-ranking-visible"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        # One short of the limit, so the correct password still gets through.
        spent = await client.post(url, data={"confirm_password": _WRONG}, follow_redirects=False)
        assert spent.status_code == 303
        ok = await client.post(url, data={"confirm_password": _TEST_PASSWORD}, follow_redirects=False)
        assert ok.status_code == 303

        # Budget restored: a full _LIMIT of failures is counted again rather
        # than refused, which without the reset would have locked on the second.
        for _ in range(_LIMIT):
            again = await client.post(url, data={"confirm_password": _WRONG}, follow_redirects=False)
            assert again.status_code == 303
        _assert_locked(await client.post(url, data={"confirm_password": _WRONG}, follow_redirects=False))


# ---------------------------------------------------------------------------
# Problem delete: the same bucket, and it must not delete while locked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_problem_delete_locks_out_and_keeps_the_problem(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The delete confirmation is throttled, and a locked actor deletes nothing."""
    _configure(monkeypatch)
    client, judge_id = await _judge_client(session, "arena-delete-throttle@test.example", password="secret")
    problem_id = await _make_problem(session, judge_id)
    url = f"/admin/problems/{problem_id}/delete"

    async with client:
        for _ in range(_LIMIT):
            wrong = await client.post(url, data={"password": _WRONG})
            assert wrong.status_code == 303

        _assert_locked(await client.post(url, data={"password": _WRONG}))
        # The correct password is refused while locked, so the problem survives.
        _assert_locked(await client.post(url, data={"password": "secret"}))

    assert await session.get(ArenaProblem, problem_id) is not None


@pytest.mark.asyncio
async def test_problem_delete_shares_the_bucket_with_rejudge_all(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Alternating delete and rejudge-all cannot double the guess budget."""
    _configure(monkeypatch)
    client, judge_id = await _judge_client(session, "arena-delete-shared@test.example", password="secret")
    problem_id = await _make_problem(session, judge_id)

    async with client:
        first = await client.post(f"/admin/problems/{problem_id}/rejudge-all", data={"password": _WRONG})
        assert first.status_code == 303
        second = await client.post(f"/admin/problems/{problem_id}/delete", data={"password": _WRONG})
        assert second.status_code == 303
        _assert_locked(await client.post(f"/admin/problems/{problem_id}/rejudge-all", data={"password": _WRONG}))
