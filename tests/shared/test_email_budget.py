#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The per-actor outbound-email budget (issue #155).

Contract: every call is counted; the ceiling depends on the tier; an actor
over its ceiling gets the seconds until the window resets; actors and tiers
are independent; a disabled policy or a ceiling of ``0`` never refuses; and
an unavailable Valkey allows the email (fail open).
"""

from __future__ import annotations

import pytest

from shared.services.email_budget import EmailBudgetPolicy, budget_key, check_email_budget

pytestmark = pytest.mark.asyncio


class _Runtime:
    def __init__(self, client) -> None:  # type: ignore[no-untyped-def]
        self._client = client

    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        return await self._client.eval(script, numkeys, *args)


class _DownRuntime:
    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        return None


class _BrokenRuntime:
    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        raise ConnectionError("valkey down")


POLICY = EmailBudgetPolicy(enabled=True, window_seconds=600, user_max=2, admin_max=3)


async def test_user_tier_refuses_past_its_ceiling_with_a_retry_after(valkey_client) -> None:  # type: ignore[no-untyped-def]
    runtime = _Runtime(valkey_client)
    verdicts = [await check_email_budget(runtime, actor_key="user:a", tier="user", policy=POLICY) for _ in range(3)]

    assert verdicts[:2] == [0, 0]
    assert 1 <= verdicts[2] <= 600
    assert await valkey_client.ttl(budget_key(tier="user", actor_key="user:a")) > 0


async def test_admin_tier_has_its_own_ceiling_and_actors_are_independent(valkey_client) -> None:  # type: ignore[no-untyped-def]
    runtime = _Runtime(valkey_client)
    admin = [await check_email_budget(runtime, actor_key="admin:x", tier="admin", policy=POLICY) for _ in range(4)]
    other = await check_email_budget(runtime, actor_key="user:b", tier="user", policy=POLICY)

    assert admin[:3] == [0, 0, 0] and admin[3] >= 1
    assert other == 0


async def test_same_actor_key_in_different_tiers_is_two_windows(valkey_client) -> None:  # type: ignore[no-untyped-def]
    runtime = _Runtime(valkey_client)
    for _ in range(2):
        await check_email_budget(runtime, actor_key="user:c", tier="user", policy=POLICY)
    assert await check_email_budget(runtime, actor_key="user:c", tier="user", policy=POLICY) >= 1
    assert await check_email_budget(runtime, actor_key="user:c", tier="admin", policy=POLICY) == 0


async def test_disabled_policy_or_zero_ceiling_never_refuses(valkey_client) -> None:  # type: ignore[no-untyped-def]
    runtime = _Runtime(valkey_client)
    disabled = EmailBudgetPolicy(enabled=False, user_max=1)
    zero = EmailBudgetPolicy(enabled=True, user_max=0)
    for _ in range(3):
        assert await check_email_budget(runtime, actor_key="user:d", tier="user", policy=disabled) == 0
        assert await check_email_budget(runtime, actor_key="user:d", tier="user", policy=zero) == 0
    assert await valkey_client.exists(budget_key(tier="user", actor_key="user:d")) == 0


async def test_unavailable_valkey_fails_open() -> None:
    for runtime in (_DownRuntime(), _BrokenRuntime(), object()):
        for _ in range(5):
            assert await check_email_budget(runtime, actor_key="user:e", tier="user", policy=POLICY) == 0
