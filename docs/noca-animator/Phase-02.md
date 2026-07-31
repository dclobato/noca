# Phase 02: Implement site settings and operator-secret services

This session implements the reusable domain operations for medal settings and
operator credentials. Both Web administration and the animator runtime use the
same shared authorization logic without importing each other.

## Source-plan coverage

This phase implements unified-plan section 4.3 and resolves its cross-module
conflict: the animator must not call `web/services/site_service.py` directly.

## Dependencies

Complete [Phase 01](Phase-01.md) first so the required columns and table exist.

## Session scope

Implement service logic and tests only. Do not create routes, forms, or
templates.

## Required preflight

Complete these checks before editing code:

1. Read `web/services/site_service.py`, `shared/services/password_service.py`,
   and current shared SQLAlchemy Core service patterns.
2. Search PyPI for token generation and secret verification libraries. Record
   why the standard library is sufficient for random opaque operator tokens.
3. Identify every existing caller of `list_contest_site_entries()` so adding
   fields doesn't break metadata-page JSON payloads.

## Service design

Keep shared behavior independent from FastAPI and Web ORM models:

- Put token generation, digesting, constant-time verification, lookup, and
  revocation in `shared/services/animator_access_service.py`.
- Accept an `AsyncSession` or `AsyncConnection` in the same style used by
  existing shared database services.
- Generate at least 256 bits of entropy with `secrets.token_urlsafe()`.
- Normalize only transport whitespace. Never lowercase or otherwise transform
  a secret.
- Return plaintext only from `create_site_secret()` and
  `create_global_secret()`.
- Use a generic failure result for invalid tokens so callers don't reveal
  whether a contest or site credential exists.

## Implementation tasks

Implement the services in this order:

1. Add shared functions to update validated medal cutoffs, list safe
   credential metadata, create a site credential, create a global credential,
   revoke a credential, and resolve a credential's authorized scope.
2. Exclude `secret_digest` from all list/view DTOs.
3. Add thin wrappers or re-exports in `web/services/site_service.py` where the
   current Web service API remains the natural caller boundary.
4. Add tests for one-time plaintext return, digest-only persistence, constant
   comparison behavior, site/global scope resolution, cross-contest rejection,
   revocation, and ordered cutoffs.
5. Update `docs/SHARED_SERVICES.md` and `web/docs/SERVICES.md`.

## Validation

Run the focused service checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/shared/test_animator_access_service.py tests/web/test_site_service.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run mypy shared web
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check shared web tests
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check shared web tests
```

Search serialized DTOs and templates for `secret_digest`; no public view may
contain it.

## Completion criteria

This phase is complete when medal configuration and scoped credential lifecycle
operations are fully tested, the Web wrapper remains compatible, and the shared
service can be called by a future animator runtime without importing `web`.

## Next phase

Continue with [Phase 03](Phase-03.md), which exposes these settings through a
dedicated contest-admin page.
