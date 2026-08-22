#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared animator access-control operations for sites and operator secrets.

This module owns the reusable domain logic that both Web administration and the
future animator runtime need without either importing the other: medal-cutoff
updates on a site, the lifecycle of scoped operator credentials, and
constant-time resolution of an operator token to its authorized scope.

Only the fixed-length ``secret_digest`` is ever persisted; the plaintext token
exists solely in the return value of a create operation. Invalid tokens resolve
to a single generic failure (``None``) so a caller can never learn whether a
contest or a site credential exists.

The functions operate over the shared SQLAlchemy Core tables and accept any
executor exposing ``execute`` (an ``AsyncSession`` or an ``AsyncConnection``),
matching the style of the other shared database services.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select, update

from shared.db_schema import contests, site_secrets, sites

# 32 bytes of ``secrets.token_urlsafe`` entropy yields a 256-bit opaque token.
_TOKEN_ENTROPY_BYTES = 32

# The fixed length of a SHA-256 hex digest, matching the schema CHECK constraint.
_DIGEST_LENGTH = 64

# Loosely typed executor: an AsyncSession (web) or AsyncConnection both expose
# ``execute``. Typed as ``object`` to avoid importing a concrete session type.
_Executor = object


class AnimatorAccessError(ValueError):
    """Raised when a medal or credential operation is invalid."""


@dataclass(frozen=True, slots=True)
class SiteSecretMetadata:
    """Safe, digest-free view of an operator secret for listing and display.

    The ``secret_digest`` column is deliberately excluded so no list or view
    DTO can leak it.
    """

    id: str
    contest_id: str
    site_id: str | None
    label: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ResolvedScope:
    """Authorized scope of a valid operator token.

    ``site_id`` is ``None`` for a contest-global control secret and set for a
    site-scoped operator secret.
    """

    contest_id: str
    site_id: str | None


def generate_operator_token() -> str:
    """Generate a fresh opaque operator token with at least 256 bits of entropy.

    Returns:
        A URL-safe random token string. The caller must persist only its digest.
    """
    return secrets.token_urlsafe(_TOKEN_ENTROPY_BYTES)


def normalize_token(raw_token: str) -> str:
    """Normalize only transport whitespace around an operator token.

    The token is never lowercased or otherwise transformed; leading and trailing
    whitespace picked up in transport (copy/paste, form fields) is stripped.

    Args:
        raw_token: The token as received from a caller.

    Returns:
        The token with surrounding whitespace removed.
    """
    return raw_token.strip()


def digest_token(raw_token: str) -> str:
    """Return the fixed-length storage digest of an operator token.

    Args:
        raw_token: The plaintext token (normalized internally).

    Returns:
        The lowercase SHA-256 hex digest of the normalized token.
    """
    normalized = normalize_token(raw_token)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def verify_digest(stored_digest: str, candidate_digest: str) -> bool:
    """Compare two digests in constant time.

    Args:
        stored_digest: The digest persisted for a credential.
        candidate_digest: The digest computed from a supplied token.

    Returns:
        True when the digests match, using a timing-safe comparison.
    """
    return hmac.compare_digest(stored_digest, candidate_digest)


def _validate_cutoffs(gold: int, silver: int, bronze: int) -> None:
    """Validate that medal cutoffs are positive and correctly ordered.

    Args:
        gold: Maximum ranking position awarded a gold medal.
        silver: Maximum ranking position awarded a silver medal.
        bronze: Maximum ranking position awarded a bronze medal.

    Raises:
        AnimatorAccessError: If any cutoff is not positive or the ordering
            ``gold <= silver <= bronze`` is violated.
    """
    if gold < 1:
        raise AnimatorAccessError("Gold cutoff must be a positive ranking position.")
    if not (gold <= silver <= bronze):
        raise AnimatorAccessError("Medal cutoffs must satisfy gold <= silver <= bronze.")


def validate_optional_cutoffs(gold: int | None, silver: int | None, bronze: int | None) -> None:
    """Validate an *optional* medal-cutoff triple, all-or-nothing.

    Global (contest-level) medals are either unconfigured -- all three values
    ``None``, meaning no medals -- or fully configured, in which case they must
    satisfy the same positivity and ordering rules a site's cutoffs do. A
    partially filled triple is always an error: it mirrors the database CHECK
    constraint, so the two boundaries cannot drift.

    Args:
        gold: Maximum ranking position awarded a gold medal, or None.
        silver: Maximum ranking position awarded a silver medal, or None.
        bronze: Maximum ranking position awarded a bronze medal, or None.

    Raises:
        AnimatorAccessError: If only some of the three values are set, or if the
            configured values are not positive and correctly ordered.
    """
    if gold is None and silver is None and bronze is None:
        return
    if gold is None or silver is None or bronze is None:
        raise AnimatorAccessError("Set all three medal cutoffs, or leave all three blank to disable medals.")
    _validate_cutoffs(gold, silver, bronze)


async def update_contest_global_medals(
    executor: _Executor,
    *,
    contest_id: str,
    gold: int | None,
    silver: int | None,
    bronze: int | None,
) -> None:
    """Update the validated contest-level (global) medal cutoffs.

    Passing all three values as ``None`` clears the configuration, disabling
    medals for the contest's global scope.

    Args:
        executor: AsyncSession or AsyncConnection with an ``execute`` method.
        contest_id: Contest to update (scopes the update).
        gold: Maximum ranking position awarded a gold medal, or None.
        silver: Maximum ranking position awarded a silver medal, or None.
        bronze: Maximum ranking position awarded a bronze medal, or None.

    Raises:
        AnimatorAccessError: If the cutoffs fail validation.
    """
    validate_optional_cutoffs(gold, silver, bronze)
    stmt = (
        update(contests)
        .where(contests.c.id == contest_id)
        .values(
            global_gold_cutoff=gold,
            global_silver_cutoff=silver,
            global_bronze_cutoff=bronze,
        )
    )
    await executor.execute(stmt)  # type: ignore[attr-defined]


async def update_site_medals(
    executor: _Executor,
    *,
    site_id: str,
    contest_id: str,
    gold: int,
    silver: int,
    bronze: int,
) -> None:
    """Update the validated medal cutoffs of a site.

    Args:
        executor: AsyncSession or AsyncConnection with an ``execute`` method.
        site_id: Identifier of the site to update.
        contest_id: Contest owning the site (scopes the update).
        gold: Maximum ranking position awarded a gold medal.
        silver: Maximum ranking position awarded a silver medal.
        bronze: Maximum ranking position awarded a bronze medal.
    Raises:
        AnimatorAccessError: If the cutoffs fail validation.
    """
    _validate_cutoffs(gold, silver, bronze)
    stmt = (
        update(sites)
        .where(sites.c.id == site_id, sites.c.contest_id == contest_id)
        .values(
            gold_cutoff=gold,
            silver_cutoff=silver,
            bronze_cutoff=bronze,
        )
    )
    await executor.execute(stmt)  # type: ignore[attr-defined]


def _row_to_metadata(row: object) -> SiteSecretMetadata:
    """Build a digest-free metadata DTO from a secret row."""
    return SiteSecretMetadata(
        id=row.id,  # type: ignore[attr-defined]
        contest_id=row.contest_id,  # type: ignore[attr-defined]
        site_id=row.site_id,  # type: ignore[attr-defined]
        label=row.label,  # type: ignore[attr-defined]
        created_at=row.created_at,  # type: ignore[attr-defined]
        updated_at=row.updated_at,  # type: ignore[attr-defined]
    )


async def list_site_secrets(
    executor: _Executor,
    contest_id: str,
    site_id: str | None = None,
) -> list[SiteSecretMetadata]:
    """List safe credential metadata for a contest, optionally scoped to a site.

    The ``secret_digest`` column is never selected, so the returned DTOs cannot
    leak it.

    Args:
        executor: AsyncSession or AsyncConnection with an ``execute`` method.
        contest_id: Contest whose secrets are listed.
        site_id: When provided, restrict to that site's secrets; otherwise list
            every secret in the contest (global and site-scoped).

    Returns:
        Credential metadata ordered oldest-first.
    """
    stmt = select(
        site_secrets.c.id,
        site_secrets.c.contest_id,
        site_secrets.c.site_id,
        site_secrets.c.label,
        site_secrets.c.created_at,
        site_secrets.c.updated_at,
    ).where(site_secrets.c.contest_id == contest_id)
    if site_id is not None:
        stmt = stmt.where(site_secrets.c.site_id == site_id)
    stmt = stmt.order_by(site_secrets.c.created_at, site_secrets.c.id)
    result = await executor.execute(stmt)  # type: ignore[attr-defined]
    return [_row_to_metadata(row) for row in result.all()]


async def _insert_secret(
    executor: _Executor,
    *,
    contest_id: str,
    site_id: str | None,
    label: str,
) -> str:
    """Insert one operator secret and return its one-time plaintext token.

    Args:
        executor: AsyncSession or AsyncConnection with an ``execute`` method.
        contest_id: Contest the secret authorizes.
        site_id: Site the secret authorizes, or ``None`` for a global secret.
        label: Human-readable label for the secret.

    Returns:
        The freshly generated plaintext token. Only its digest is persisted.

    Raises:
        AnimatorAccessError: If the label is blank.
    """
    cleaned_label = label.strip()
    if not cleaned_label:
        raise AnimatorAccessError("Secret label is required.")
    token = generate_operator_token()
    now = datetime.now(UTC)
    stmt = site_secrets.insert().values(
        contest_id=contest_id,
        site_id=site_id,
        secret_digest=digest_token(token),
        label=cleaned_label,
        created_at=now,
        updated_at=now,
    )
    await executor.execute(stmt)  # type: ignore[attr-defined]
    return token


async def create_site_secret(
    executor: _Executor,
    *,
    contest_id: str,
    site_id: str,
    label: str,
) -> str:
    """Create a site-scoped operator secret and return its plaintext once.

    Args:
        executor: AsyncSession or AsyncConnection with an ``execute`` method.
        contest_id: Contest owning the site.
        site_id: Site this secret authorizes control of.
        label: Human-readable label for the secret.

    Returns:
        The one-time plaintext token; only its digest is persisted.
    """
    return await _insert_secret(executor, contest_id=contest_id, site_id=site_id, label=label)


async def create_global_secret(
    executor: _Executor,
    *,
    contest_id: str,
    label: str,
) -> str:
    """Create a contest-global control secret and return its plaintext once.

    Args:
        executor: AsyncSession or AsyncConnection with an ``execute`` method.
        contest_id: Contest this global control secret authorizes.
        label: Human-readable label for the secret.

    Returns:
        The one-time plaintext token; only its digest is persisted.
    """
    return await _insert_secret(executor, contest_id=contest_id, site_id=None, label=label)


async def revoke_secret(executor: _Executor, *, contest_id: str, secret_id: str) -> bool:
    """Revoke (delete) an operator secret scoped to its owning contest.

    The delete is scoped by ``contest_id`` so an administrator of one contest can
    never revoke another contest's credential by supplying its identifier.

    Args:
        executor: AsyncSession or AsyncConnection with an ``execute`` method.
        contest_id: Contest that must own the secret for the revocation to apply.
        secret_id: Identifier of the secret to remove.

    Returns:
        True when a matching secret was removed; False when no secret with that
        identifier exists in the given contest.
    """
    stmt = delete(site_secrets).where(
        site_secrets.c.id == secret_id,
        site_secrets.c.contest_id == contest_id,
    )
    result = await executor.execute(stmt)  # type: ignore[attr-defined]
    return bool(result.rowcount)


async def revoke_all_secrets(executor: _Executor, *, contest_id: str) -> int:
    """Revoke every operator secret owned by a contest.

    Args:
        executor: AsyncSession or AsyncConnection with an ``execute`` method.
        contest_id: Contest whose global and site-scoped secrets are removed.

    Returns:
        Number of credentials revoked.
    """
    result = await executor.execute(  # type: ignore[attr-defined]
        delete(site_secrets).where(site_secrets.c.contest_id == contest_id)
    )
    return max(0, int(result.rowcount or 0))


async def resolve_scope(
    executor: _Executor,
    contest_id: str,
    token: str,
) -> ResolvedScope | None:
    """Resolve an operator token to its authorized scope within a contest.

    The token is digested and matched against the contest's stored digests with
    a timing-safe comparison. A token that does not match any credential in the
    contest — including one belonging to a different contest — resolves to the
    single generic failure ``None`` so callers cannot tell whether a contest or
    site credential exists.

    Args:
        executor: AsyncSession or AsyncConnection with an ``execute`` method.
        contest_id: Contest the token is presented against.
        token: The plaintext operator token supplied by the caller.

    Returns:
        The authorized ``ResolvedScope`` (``site_id`` set for a site secret,
        ``None`` for a global secret), or ``None`` when the token is invalid.
    """
    candidate_digest = digest_token(token)
    if len(candidate_digest) != _DIGEST_LENGTH:  # pragma: no cover - sha256 is fixed length
        return None
    stmt = select(
        site_secrets.c.site_id,
        site_secrets.c.secret_digest,
    ).where(site_secrets.c.contest_id == contest_id)
    result = await executor.execute(stmt)  # type: ignore[attr-defined]
    match: str | None = None
    matched = False
    for row in result.all():
        if verify_digest(row.secret_digest, candidate_digest):
            match = row.site_id
            matched = True
    if not matched:
        return None
    return ResolvedScope(contest_id=contest_id, site_id=match)
