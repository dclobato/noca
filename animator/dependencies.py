#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reusable FastAPI dependencies for the animator runtime.

The public-route limiter (feeds and team media) lives here too. Its policy is rebuilt from ``settings``
on every call and its fallback limiter is module-level, mirroring the health
monitor and Arena signup limiters, so tests can monkeypatch the knobs and reset
the in-memory state between cases.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from animator.config import settings
from animator.database import get_db
from animator.models.command_receipt import IDEMPOTENCY_KEY_PATTERN
from animator.models.controller_lease import CONTROLLER_ID_HEADER, CONTROLLER_ID_PATTERN
from animator.models.query_records import ContestRecord
from animator.services.contest_feed_service import load_enabled_contest
from animator.services.control_audit import note_control_outcome, scope_label
from animator.services.controller_lease_service import ControllerLeaseService
from animator.services.feed_cache import AnimatorFeedCache
from animator.services.projector_presence import ProjectorPresence
from animator.services.public_scope_service import PublicScope, resolve_public_scope
from animator.services.reveal_session_store import RevealSessionStore, RevealStoreClient
from animator.services.sse_capacity import SseCapacity
from shared.reveal_schema import GLOBAL_SCOPE
from shared.services.animator_access_service import ResolvedScope, resolve_scope
from shared.services.auth_rate_limit import (
    AuthRateLimitSettings,
    AuthThrottleIdentity,
    InMemoryAuthRateLimiter,
    build_auth_throttle_identity,
    check_auth_throttle,
    record_auth_failure,
    reset_auth_throttle,
)
from shared.services.request_rate_limit import (
    InMemoryRateLimiter,
    RateLimitPolicy,
    enforce_ip_rate_limit,
    parse_trusted_cidrs,
)
from shared.services.sse_connection_limit import SseSlotPolicy, sse_connection_slots
from shared.services.valkey_service import ValkeyRuntime

PUBLIC_RATE_LIMIT_BUCKET = "animator:public"
PUBLIC_RATE_LIMITER = InMemoryRateLimiter()
PUBLIC_RATE_LIMIT_DETAIL = "Animator public rate limit exceeded."


def _public_rate_limit_policy() -> RateLimitPolicy:
    """Build the public-route bucket policy from the current settings."""
    return RateLimitPolicy(
        bucket=PUBLIC_RATE_LIMIT_BUCKET,
        max_requests=settings.PUBLIC_RATE_LIMIT_MAX_REQUESTS,
        window_seconds=settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS,
        trusted_networks=parse_trusted_cidrs(settings.PUBLIC_RATE_LIMIT_TRUSTED_CIDRS),
        enabled=settings.PUBLIC_RATE_LIMIT_ENABLED,
    )


async def enforce_public_rate_limit(request: Request) -> None:
    """Apply the shared per-IP window to the anonymous feeds and team-media routes.

    Installed as a route-level ``dependencies=[...]`` entry, so it runs *before*
    the contest gate. That is deliberate: the answer depends on the client IP
    alone, never on the slug or scope, so a ``429`` cannot be used to probe the
    non-enumerating ``404``, and a flood has to be stopped ahead of the gate's
    own query or the limiter would protect nothing.

    Raises:
        HTTPException: ``429`` with ``Retry-After`` once the window is spent.
    """
    await enforce_ip_rate_limit(
        request,
        policy=_public_rate_limit_policy(),
        fallback_limiter=PUBLIC_RATE_LIMITER,
        detail=PUBLIC_RATE_LIMIT_DETAIL,
    )


SSE_LIMIT_BUCKET = "animator:sse"
SSE_LIMIT_DETAIL = "Too many open animator event streams."


def _sse_slot_policy() -> SseSlotPolicy:
    """Build the SSE connection-cap policy from the current settings."""
    return SseSlotPolicy(
        bucket=SSE_LIMIT_BUCKET,
        max_per_ip=settings.SSE_MAX_PER_IP,
        max_per_user=1,
        ttl_seconds=settings.SSE_CONNECTION_TTL_SECONDS,
        trusted_networks=parse_trusted_cidrs(settings.SSE_TRUSTED_CIDRS),
        enabled=settings.SSE_LIMIT_ENABLED,
    )


def get_sse_capacity(request: Request) -> SseCapacity:
    """Return the process-wide SSE client gauge, creating it on first use.

    The lifespan installs it on ``app.state``; an app assembled without the
    lifespan (tests, tooling) gets one lazily from the configured ceiling so the
    stream routes never depend on startup order.
    """
    capacity: SseCapacity | None = getattr(request.app.state, "sse_capacity", None)
    if capacity is None:
        capacity = SseCapacity(settings.MAX_SSE_CLIENTS)
        request.app.state.sse_capacity = capacity
    return capacity


async def enforce_sse_connection_caps(request: Request) -> AsyncIterator[None]:
    """Hold the process ceiling and the per-IP lease for one anonymous stream.

    Installed as a route-level ``dependencies=[...]`` entry on ``/events`` and
    ``/reveal/events``, so both refusals happen *before* the contest gate: the
    answer depends on the client IP and this process's load alone, never on the
    slug, so a ``503``/``429`` cannot probe the non-enumerating ``404``. As a
    yield dependency its teardown runs when the streamed response ends -- that
    is, when the client disconnects -- which is what frees the slots.

    The process ceiling is checked first because it never needs Valkey; the
    per-IP lease is Valkey-backed and fails open.

    Raises:
        HTTPException: ``503`` when this process is full; ``429`` when the
            client IP already holds ``NOCA_ANIMATOR_SSE_MAX_PER_IP`` streams.
    """
    capacity = get_sse_capacity(request)
    async with capacity.slot(), sse_connection_slots(request, policy=_sse_slot_policy(), detail=SSE_LIMIT_DETAIL):
        yield


def get_feed_cache(request: Request) -> AnimatorFeedCache:
    """Return the process-wide feed cache from application state."""
    cache: AnimatorFeedCache = request.app.state.feed_cache
    return cache


FeedCache = Annotated[AnimatorFeedCache, Depends(get_feed_cache)]


def get_valkey_runtime(request: Request) -> ValkeyRuntime:
    """Return the process-wide Valkey runtime from application state.

    Args:
        request: Current FastAPI request.

    Returns:
        ValkeyRuntime: The runtime started during the app lifespan.
    """
    runtime: ValkeyRuntime = request.app.state.valkey_runtime
    return runtime


DbSession = Annotated[AsyncSession, Depends(get_db)]
Valkey = Annotated[ValkeyRuntime, Depends(get_valkey_runtime)]


async def get_enabled_contest_detached(slug: str, request: Request) -> ContestRecord:
    """Resolve an enabled contest for a streaming route without holding a session.

    A ``yield``-style ``DbSession`` dependency stays alive for the whole response,
    which for an SSE stream means pinning a pooled PostgreSQL connection for the
    entire client connection. This dependency instead opens its own session inside
    an ``async with``, resolves the contest into the detached, frozen
    ``ContestRecord``, and closes the session **before** streaming begins, so a
    long-lived SSE connection holds no database resources.

    The ``404`` behavior is identical to :func:`get_enabled_contest`: a missing
    slug and an animator-disabled contest are indistinguishable.

    Args:
        slug: Public contest slug from the router path.
        request: Current request, used to reach ``app.state.db_session``.

    Returns:
        The resolved enabled contest record.

    Raises:
        HTTPException: ``404`` when the contest is missing or animator-disabled.
    """
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.db_session
    async with session_factory() as session:
        contest = await load_enabled_contest(session, slug)
    if contest is None:
        raise HTTPException(status_code=404)
    return contest


DetachedEnabledContest = Annotated[ContestRecord, Depends(get_enabled_contest_detached)]


async def get_enabled_contest(slug: str, db: DbSession) -> ContestRecord:
    """Resolve an animator-enabled contest by slug or raise a bare ``404``.

    The gate is intentionally non-enumerating: an unknown slug and a disabled
    contest raise the identical, non-specific default ``404`` response (FastAPI's
    standard ``{"detail": "Not Found"}`` body), so a caller cannot tell which
    condition failed. All later public, media, event, and reveal routes reuse
    this dependency to inherit the same behavior.

    Args:
        slug: Public contest slug from the router path.
        db: Active database session.

    Returns:
        The resolved enabled contest record.

    Raises:
        HTTPException: ``404`` when the contest is missing or animator-disabled.
    """
    contest = await load_enabled_contest(db, slug)
    if contest is None:
        raise HTTPException(status_code=404)
    return contest


EnabledContest = Annotated[ContestRecord, Depends(get_enabled_contest)]


async def get_public_scope(contest: EnabledContest, db: DbSession, scope: str = GLOBAL_SCOPE) -> PublicScope:
    """Resolve the spectator ``?scope=`` query value for an enabled contest.

    ``scope`` is declared as a plain, unconstrained ``str`` on purpose. A
    ``Literal``/pattern-validated parameter would make FastAPI answer ``422``
    *before* the contest gate ran, so a malformed scope would prove that the slug
    resolved — exactly the enumeration the bare ``404`` exists to prevent. The
    value is parsed permissively here and judged only after ``EnabledContest``.

    Args:
        contest: The resolved enabled contest (gate already passed).
        db: Active database session.
        scope: ``"global"`` (default) or a site id of this contest.

    Returns:
        The resolved scope.

    Raises:
        HTTPException: ``404`` when the value names no site of this contest —
            the identical response an unknown slug gets.
    """
    resolved = await resolve_public_scope(db, contest, scope)
    if resolved is None:
        raise HTTPException(status_code=404)
    return resolved


PublicScopeDep = Annotated[PublicScope, Depends(get_public_scope)]


async def get_public_scope_detached(
    contest: DetachedEnabledContest,
    request: Request,
    scope: str = GLOBAL_SCOPE,
) -> PublicScope:
    """Resolve a spectator scope for a streaming route without holding a session.

    The streaming counterpart of :func:`get_public_scope`, for the same reason
    :func:`get_enabled_contest_detached` exists: a ``yield``-style ``DbSession``
    would pin a pooled PostgreSQL connection for the entire life of an SSE
    connection. This opens its own session, resolves the scope, and closes it
    before streaming begins.

    Args:
        contest: The detached enabled contest (resolved without holding a session).
        request: Current request, used to reach ``app.state.db_session``.
        scope: ``"global"`` (default) or a site id of this contest.

    Returns:
        The resolved scope.

    Raises:
        HTTPException: ``404`` when the value names no site of this contest.
    """
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.db_session
    async with session_factory() as session:
        resolved = await resolve_public_scope(session, contest, scope)
    if resolved is None:
        raise HTTPException(status_code=404)
    return resolved


DetachedPublicScope = Annotated[PublicScope, Depends(get_public_scope_detached)]


def get_reveal_store(valkey: Valkey) -> RevealSessionStore:
    """Build the durable reveal-session store over the process Valkey runtime.

    Args:
        valkey: The process-wide Valkey runtime (satisfies ``RevealStoreClient``).

    Returns:
        A store bound to the runtime and the configured TTL margin.
    """
    client: RevealStoreClient = valkey
    return RevealSessionStore(client, ttl_margin_seconds=settings.REVEAL_TTL_MARGIN_SECONDS)


RevealStore = Annotated[RevealSessionStore, Depends(get_reveal_store)]


def get_controller_lease_service(valkey: Valkey) -> ControllerLeaseService:
    """Build the scoped controller-lease service over the Valkey runtime."""
    return ControllerLeaseService(valkey, ttl_seconds=settings.CONTROLLER_LEASE_TTL_SECONDS)


ControllerLease = Annotated[ControllerLeaseService, Depends(get_controller_lease_service)]


def get_projector_presence(request: Request) -> ProjectorPresence:
    """Build the per-scope projector presence gauge over the Valkey runtime.

    Reads the runtime off ``app.state`` directly rather than through ``Valkey``
    so the detached ``/reveal/events`` stream can use it without a session.
    """
    runtime: ValkeyRuntime = request.app.state.valkey_runtime
    return ProjectorPresence(runtime, ttl_seconds=settings.PROJECTOR_PRESENCE_TTL_SECONDS)


ProjectorPresenceDep = Annotated[ProjectorPresence, Depends(get_projector_presence)]


async def get_control_contest(request: Request, contest: EnabledContest) -> ContestRecord:
    """Resolve an enabled contest and then apply the control kill switch.

    The order is required by the phase contract and is the reason this is a
    dependency rather than a router-level one: FastAPI resolves router
    ``dependencies=[...]`` *before* parameter dependencies, so a router-level
    kill switch would run ahead of the contest gate. Depending on
    ``EnabledContest`` here forces contest resolution first, then the switch.

    Both refusals are the same bare ``404`` — the default ``{"detail": "Not
    Found"}`` — so a caller can distinguish neither an unknown slug from a
    disabled contest, nor either of those from a deployment that simply has
    control switched off.

    Args:
        request: Current request, for the audit context.
        contest: The resolved enabled contest.

    Returns:
        The same contest record, once control is permitted.

    Raises:
        HTTPException: ``404`` when ``NOCA_ANIMATOR_ENABLE_CONTROL`` is false.
    """
    note_control_outcome(request, contest_id=contest.id)
    if not settings.ENABLE_CONTROL:
        note_control_outcome(request, outcome="control_disabled")
        raise HTTPException(status_code=404)
    return contest


ControlContest = Annotated[ContestRecord, Depends(get_control_contest)]


_operator_bearer = HTTPBearer(
    scheme_name="AnimatorOperatorToken",
    description="Reveal operator token issued for a site or for contest-global control.",
    auto_error=False,
)
"""Bearer extractor for operator tokens.

``auto_error=False`` on purpose: the built-in error would answer a missing or
malformed header *before* the contest gate had its say, turning an
animator-disabled contest into an authentication-shaped ``403`` and leaking that
the contest exists. With it off, the header is merely parsed and this module
decides, after the gate, on one uniform ``403``.
"""

OperatorCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_operator_bearer)]


FORBIDDEN_DETAIL = "Invalid operator credential"
"""One detail string for every authorization refusal, so an unknown token, a
wrong scheme, a scope mismatch, and a locked-out address are indistinguishable
to the caller."""

CONTROL_LOCKOUT_LIMITER = InMemoryAuthRateLimiter()
"""Process-local fallback for the operator-token lockout; tests reset it."""


def _control_lockout_settings() -> AuthRateLimitSettings:
    """Build the IP-only lockout policy for the operator-token gate.

    The identity carries no account component (``identifier=None``), so the
    account cap and the HMAC secret are never used; the lockout duration doubles
    as the window the failures are counted in.
    """
    return AuthRateLimitSettings(
        enabled=settings.CONTROL_LOCKOUT_ENABLED,
        window_seconds=settings.CONTROL_LOCKOUT_SECONDS,
        ip_max_failures=settings.CONTROL_LOCKOUT_FAILURES,
        account_max_failures=settings.CONTROL_LOCKOUT_FAILURES,
        lockout_seconds=settings.CONTROL_LOCKOUT_SECONDS,
        secret="",  # unused: IP-only identity, no account hash
    )


def _control_lockout_identity(request: Request, throttle_settings: AuthRateLimitSettings) -> AuthThrottleIdentity:
    """The per-IP identity of the control gate (``auth:rate-limit:animator:control:ip:…``)."""
    return build_auth_throttle_identity(
        request, module="animator", action="control", identifier=None, settings=throttle_settings
    )


async def resolve_operator_scope(
    request: Request,
    contest: ControlContest,
    db: DbSession,
    credentials: OperatorCredentials,
) -> ResolvedScope:
    """Resolve the bearer operator token to the scope it authorizes.

    Dependency order is load-bearing: ``ControlContest`` is a sub-dependency
    resolved *before* this function's body, so a missing slug, an
    animator-disabled contest, and a switched-off deployment all answer ``404``
    without any token ever being looked up.

    Every authentication failure — absent header, wrong scheme, blank token,
    unknown token, token belonging to another contest — produces the identical
    ``403`` with no detail, and each one is audited exactly like an accepted
    attempt (with ``scope=unknown``), because the refusal is *noted* here and
    emitted by the audit boundary that wraps the whole request.

    Failures are counted per client IP. Once ``NOCA_ANIMATOR_CONTROL_LOCKOUT_FAILURES``
    of them land inside one window the address is locked out for
    ``NOCA_ANIMATOR_CONTROL_LOCKOUT_SECONDS``: the lockout is checked *before*
    the header is inspected — but after ``ControlContest``, so the contest gate
    and the kill switch still answer ``404`` first and the lockout cannot probe
    them — and it answers the very same generic ``403`` (no ``Retry-After``),
    audited as ``outcome=throttled``. A valid token resets the counter; scope
    and controller-ownership refusals are not counted, since their token
    authenticated.

    Args:
        request: Current request, for the audit record.
        contest: The resolved enabled contest (both gates already passed).
        db: Active database session.
        credentials: Parsed ``Authorization`` header, or ``None``.

    Returns:
        The authorized scope: ``site_id`` set for a site credential, ``None``
        for a contest-global control credential.

    Raises:
        HTTPException: ``403`` for any invalid or missing credential.
    """
    throttle_settings = _control_lockout_settings()
    identity = _control_lockout_identity(request, throttle_settings)
    lockout = await check_auth_throttle(
        request, identity, settings=throttle_settings, fallback_limiter=CONTROL_LOCKOUT_LIMITER
    )
    if not lockout.allowed:
        note_control_outcome(request, outcome="throttled")
        raise HTTPException(status_code=403, detail=FORBIDDEN_DETAIL)

    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials.strip():
        raise await _refuse_credential(request, identity, throttle_settings)
    scope = await resolve_scope(db, contest.id, credentials.credentials)
    if scope is None:
        raise await _refuse_credential(request, identity, throttle_settings)
    await reset_auth_throttle(request, identity, fallback_limiter=CONTROL_LOCKOUT_LIMITER)
    note_control_outcome(request, scope=scope_label(scope.site_id))
    return scope


async def _refuse_credential(
    request: Request, identity: AuthThrottleIdentity, throttle_settings: AuthRateLimitSettings
) -> HTTPException:
    """Count and note one rejected credential, and build its uniform ``403``.

    The record itself is emitted by the audit boundary, so a refusal here and a
    refusal in a route cannot produce two lines for one request.
    """
    await record_auth_failure(request, identity, settings=throttle_settings, fallback_limiter=CONTROL_LOCKOUT_LIMITER)
    note_control_outcome(request, outcome="invalid_credential")
    return HTTPException(status_code=403, detail=FORBIDDEN_DETAIL)


OperatorScope = Annotated[ResolvedScope, Depends(resolve_operator_scope)]


def get_idempotency_key(
    request: Request,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=IDEMPOTENCY_KEY_PATTERN,
            description="Optional caller-chosen key identifying one command attempt, so a retry replays it.",
        ),
    ] = None,
) -> str | None:
    """Validate the optional ``Idempotency-Key`` header of a mutating command.

    The key is a header rather than a body field so that all five mutating
    commands accept it uniformly, including the three whose body is empty by
    design — and so that a key never has to be smuggled into a model that
    deliberately forbids extra fields.

    It is **not** a credential: it is caller-chosen, carries no authority, and
    identifies nothing but one command attempt. It is therefore safe to record in
    the ceremony state, and it is still never logged, because a value an operator
    picked is not the audit log's business.

    A malformed key is rejected by FastAPI with ``422`` before this body runs — a
    *stated* refusal that changed nothing, which is exactly how the operator panel
    treats it.

    Args:
        request: Current request, for the audit context.
        idempotency_key: The validated header value, when present.

    Returns:
        The key, or ``None`` when the caller sent none.
    """
    note_control_outcome(request, idempotent=idempotency_key is not None)
    return idempotency_key


IdempotencyKey = Annotated[str | None, Depends(get_idempotency_key)]


def get_controller_id(
    controller_id: Annotated[
        str,
        Header(
            alias=CONTROLLER_ID_HEADER,
            min_length=8,
            max_length=128,
            pattern=CONTROLLER_ID_PATTERN,
            description="Opaque per-panel identifier for active controller ownership.",
        ),
    ],
) -> str:
    """Return the validated controller id without logging or persisting it."""
    return controller_id


ControllerId = Annotated[str, Depends(get_controller_id)]
