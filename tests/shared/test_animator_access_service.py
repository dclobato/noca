#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the shared animator access-control service.

These cover the reusable domain contract independent of the Web ORM wrappers:
one-time plaintext return, digest-only persistence, constant-time comparison,
site vs. global scope resolution, cross-contest rejection, revocation, ordered
cutoff validation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import site_secrets
from shared.services import animator_access_service as access
from shared.services.animator_access_service import AnimatorAccessError
from web.models.contest import Contest
from web.models.site import Site
from web.models.users import UberAdmin


async def _make_contest(session: AsyncSession, uberadmin: UberAdmin, slug: str) -> Contest:
    """Persist a minimal running contest and return it flushed."""
    contest = Contest(
        contest_name=f"Contest {slug}",
        contest_url=f"http://{slug}.example.com",
        login_slug=slug,
        start_time=datetime.now(UTC) - timedelta(minutes=30),
        duration_minutes=120,
        stop_answers_after=120,
        stop_updating_scoreboard=120,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    session.add(contest)
    await session.flush()
    return contest


async def _make_site(session: AsyncSession, contest: Contest, name: str) -> Site:
    """Persist a site under the given contest and return it flushed."""
    site = Site(
        sitename=name,
        sitename_normalized=name.lower(),
        contest_id=contest.id,
    )
    session.add(site)
    await session.flush()
    return site


# ---------------------------------------------------------------------------
# Token generation, digesting, and comparison
# ---------------------------------------------------------------------------


def test_generate_operator_token_has_high_entropy() -> None:
    """Generated tokens are long, URL-safe, and distinct across calls."""
    tokens = {access.generate_operator_token() for _ in range(50)}
    assert len(tokens) == 50
    for token in tokens:
        # 32 bytes base64url-encoded is >= 43 chars of at least 256-bit entropy.
        assert len(token) >= 43


def test_digest_is_fixed_length_and_normalizes_whitespace() -> None:
    """The digest is a 64-char SHA-256 hex, ignoring transport whitespace only."""
    digest = access.digest_token("abc")
    assert len(digest) == 64
    assert access.digest_token("  abc  ") == digest
    # Case is meaningful: a token is never lowercased.
    assert access.digest_token("ABC") != digest


def test_verify_digest_constant_comparison() -> None:
    """verify_digest returns True only for an exact digest match."""
    digest = access.digest_token("token-value")
    assert access.verify_digest(digest, access.digest_token("token-value")) is True
    assert access.verify_digest(digest, access.digest_token("other-value")) is False


def test_verify_digest_uses_hmac_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """The comparison must go through hmac.compare_digest, not a bare ``==``.

    Spy on ``hmac.compare_digest`` so a regression to ``==`` (which is not
    timing-safe) fails this test rather than passing silently.
    """
    calls: list[tuple[str, str]] = []
    real_compare = access.hmac.compare_digest

    def _spy(a: str, b: str) -> bool:
        calls.append((a, b))
        return real_compare(a, b)

    monkeypatch.setattr(access.hmac, "compare_digest", _spy)

    digest = access.digest_token("token-value")
    assert access.verify_digest(digest, digest) is True
    assert calls == [(digest, digest)]


# ---------------------------------------------------------------------------
# Medal cutoff updates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_site_medals_persists_ordered_values(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Valid, ordered cutoffs are written back to the site."""
    contest = await _make_contest(session, uberadmin, "medals-ok")
    site = await _make_site(session, contest, "Campus A")

    await access.update_site_medals(
        session,
        site_id=site.id,
        contest_id=contest.id,
        gold=2,
        silver=4,
        bronze=6,
    )
    await session.refresh(site)
    assert (site.gold_cutoff, site.silver_cutoff, site.bronze_cutoff) == (2, 4, 6)


@pytest.mark.asyncio
async def test_update_site_medals_rejects_unordered_cutoffs(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """gold > silver is rejected before touching the database."""
    contest = await _make_contest(session, uberadmin, "medals-order")
    site = await _make_site(session, contest, "Campus B")
    with pytest.raises(AnimatorAccessError):
        await access.update_site_medals(
            session,
            site_id=site.id,
            contest_id=contest.id,
            gold=5,
            silver=2,
            bronze=3,
        )


@pytest.mark.asyncio
async def test_update_site_medals_rejects_non_positive_cutoff(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A zero gold cutoff is rejected by the positivity guard."""
    contest = await _make_contest(session, uberadmin, "medals-positive")
    site = await _make_site(session, contest, "Campus C")
    with pytest.raises(AnimatorAccessError):
        await access.update_site_medals(
            session,
            site_id=site.id,
            contest_id=contest.id,
            gold=0,
            silver=2,
            bronze=3,
        )


# ---------------------------------------------------------------------------
# Global (contest-level) medal cutoffs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("gold", "silver", "bronze"),
    [(None, None, None), (1, 2, 3), (4, 8, 12), (1, 1, 1)],
    ids=["all-blank", "defaults", "wide-bands", "single-position"],
)
def test_validate_optional_cutoffs_accepts_none_or_a_full_ordered_triple(
    gold: int | None, silver: int | None, bronze: int | None
) -> None:
    """Global medals are all-or-nothing: no values, or three ordered ones."""
    access.validate_optional_cutoffs(gold, silver, bronze)


@pytest.mark.parametrize(
    ("gold", "silver", "bronze"),
    [(1, 2, None), (1, None, 3), (None, 2, 3), (1, None, None), (None, None, 3)],
    ids=["missing-bronze", "missing-silver", "missing-gold", "gold-only", "bronze-only"],
)
def test_validate_optional_cutoffs_rejects_a_partial_triple(
    gold: int | None, silver: int | None, bronze: int | None
) -> None:
    """A half-filled triple is an error, mirroring the database CHECK."""
    with pytest.raises(AnimatorAccessError):
        access.validate_optional_cutoffs(gold, silver, bronze)


@pytest.mark.parametrize(
    ("gold", "silver", "bronze"),
    [(5, 2, 3), (0, 2, 3), (1, 9, 3)],
    ids=["gold-above-silver", "gold-not-positive", "silver-above-bronze"],
)
def test_validate_optional_cutoffs_applies_the_site_rules_when_configured(gold: int, silver: int, bronze: int) -> None:
    """Configured global cutoffs must be positive and ordered, like a site's."""
    with pytest.raises(AnimatorAccessError):
        access.validate_optional_cutoffs(gold, silver, bronze)


@pytest.mark.asyncio
async def test_update_contest_global_medals_persists_ordered_values(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    """Valid, ordered cutoffs are written back to the contest."""
    contest = await _make_contest(session, uberadmin, "global-medals-ok")

    await access.update_contest_global_medals(session, contest_id=contest.id, gold=2, silver=4, bronze=6)
    await session.refresh(contest)

    assert (contest.global_gold_cutoff, contest.global_silver_cutoff, contest.global_bronze_cutoff) == (2, 4, 6)


@pytest.mark.asyncio
async def test_update_contest_global_medals_clears_with_all_none(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Passing all three as None disables global medals again."""
    contest = await _make_contest(session, uberadmin, "global-medals-clear")
    await access.update_contest_global_medals(session, contest_id=contest.id, gold=2, silver=4, bronze=6)

    await access.update_contest_global_medals(session, contest_id=contest.id, gold=None, silver=None, bronze=None)
    await session.refresh(contest)

    assert contest.global_gold_cutoff is None
    assert contest.global_silver_cutoff is None
    assert contest.global_bronze_cutoff is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gold", "silver", "bronze"),
    [(1, 2, None), (5, 2, 3), (0, 2, 3)],
    ids=["partial", "unordered", "non-positive"],
)
async def test_update_contest_global_medals_rejects_invalid_input(
    session: AsyncSession,
    uberadmin: UberAdmin,
    gold: int | None,
    silver: int | None,
    bronze: int | None,
) -> None:
    """Invalid input is rejected before the database is touched."""
    contest = await _make_contest(session, uberadmin, f"global-medals-bad-{gold}-{silver}-{bronze}")

    with pytest.raises(AnimatorAccessError):
        await access.update_contest_global_medals(
            session, contest_id=contest.id, gold=gold, silver=silver, bronze=bronze
        )
    await session.refresh(contest)
    assert contest.global_gold_cutoff is None


# ---------------------------------------------------------------------------
# Credential lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_site_secret_returns_plaintext_and_stores_digest_only(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    """create_site_secret returns the plaintext once; only its digest persists."""
    contest = await _make_contest(session, uberadmin, "secret-create")
    site = await _make_site(session, contest, "Campus D")

    token = await access.create_site_secret(session, contest_id=contest.id, site_id=site.id, label="Operador D")
    assert token

    rows = (
        await session.execute(
            select(site_secrets.c.secret_digest, site_secrets.c.site_id).where(site_secrets.c.contest_id == contest.id)
        )
    ).all()
    assert len(rows) == 1
    stored_digest, stored_site_id = rows[0]
    # The plaintext is never stored; the stored digest matches the token digest.
    assert stored_digest != token
    assert stored_digest == access.digest_token(token)
    assert stored_site_id == site.id


@pytest.mark.asyncio
async def test_create_global_secret_has_null_site(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A global control secret is stored with site_id NULL."""
    contest = await _make_contest(session, uberadmin, "secret-global-create")
    token = await access.create_global_secret(session, contest_id=contest.id, label="Global")

    stored_site_id = await session.scalar(select(site_secrets.c.site_id).where(site_secrets.c.contest_id == contest.id))
    assert stored_site_id is None
    assert token


@pytest.mark.asyncio
async def test_create_secret_rejects_blank_label(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A blank label is refused."""
    contest = await _make_contest(session, uberadmin, "secret-blank")
    with pytest.raises(AnimatorAccessError):
        await access.create_global_secret(session, contest_id=contest.id, label="   ")


@pytest.mark.asyncio
async def test_list_site_secrets_excludes_digest(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Listing returns metadata DTOs that carry no digest attribute."""
    contest = await _make_contest(session, uberadmin, "secret-list")
    site = await _make_site(session, contest, "Campus E")
    await access.create_site_secret(session, contest_id=contest.id, site_id=site.id, label="One")
    await access.create_global_secret(session, contest_id=contest.id, label="Global")

    site_scoped = await access.list_site_secrets(session, contest.id, site.id)
    assert len(site_scoped) == 1
    entry = site_scoped[0]
    assert entry.label == "One"
    assert entry.site_id == site.id
    assert not hasattr(entry, "secret_digest")

    all_secrets = await access.list_site_secrets(session, contest.id)
    assert len(all_secrets) == 2


@pytest.mark.asyncio
async def test_revoke_secret_removes_row(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """revoke_secret deletes the credential and reports success."""
    contest = await _make_contest(session, uberadmin, "secret-revoke")
    await access.create_global_secret(session, contest_id=contest.id, label="Global")
    secret_id = await session.scalar(select(site_secrets.c.id).where(site_secrets.c.contest_id == contest.id))
    assert secret_id is not None

    assert await access.revoke_secret(session, contest_id=contest.id, secret_id=secret_id) is True
    remaining = await access.list_site_secrets(session, contest.id)
    assert remaining == []


@pytest.mark.asyncio
async def test_revoke_secret_rejects_cross_contest(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A secret cannot be revoked under a contest that does not own it."""
    contest_a = await _make_contest(session, uberadmin, "revoke-a")
    contest_b = await _make_contest(session, uberadmin, "revoke-b")
    await access.create_global_secret(session, contest_id=contest_b.id, label="B")
    secret_id = await session.scalar(select(site_secrets.c.id).where(site_secrets.c.contest_id == contest_b.id))
    assert secret_id is not None

    # Presenting contest_a as the scope must not delete contest_b's secret.
    assert await access.revoke_secret(session, contest_id=contest_a.id, secret_id=secret_id) is False
    assert len(await access.list_site_secrets(session, contest_b.id)) == 1


# ---------------------------------------------------------------------------
# Scope resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_scope_site_and_global(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A valid token resolves to its exact scope (site vs. global)."""
    contest = await _make_contest(session, uberadmin, "scope-ok")
    site = await _make_site(session, contest, "Campus F")
    site_token = await access.create_site_secret(session, contest_id=contest.id, site_id=site.id, label="Site")
    global_token = await access.create_global_secret(session, contest_id=contest.id, label="Global")

    site_scope = await access.resolve_scope(session, contest.id, site_token)
    assert site_scope is not None
    assert site_scope.site_id == site.id

    global_scope = await access.resolve_scope(session, contest.id, global_token)
    assert global_scope is not None
    assert global_scope.site_id is None


@pytest.mark.asyncio
async def test_resolve_scope_generic_failure_for_invalid_token(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """An unknown token resolves to the generic failure None."""
    contest = await _make_contest(session, uberadmin, "scope-bad")
    await access.create_global_secret(session, contest_id=contest.id, label="Global")
    assert await access.resolve_scope(session, contest.id, "not-a-real-token") is None


@pytest.mark.asyncio
async def test_resolve_scope_rejects_cross_contest_token(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A token minted for contest A does not resolve under contest B."""
    contest_a = await _make_contest(session, uberadmin, "scope-a")
    contest_b = await _make_contest(session, uberadmin, "scope-b")
    token = await access.create_global_secret(session, contest_id=contest_a.id, label="A")

    assert await access.resolve_scope(session, contest_a.id, token) is not None
    assert await access.resolve_scope(session, contest_b.id, token) is None
