# Phase 03: Add the Web animator administration page

This session gives contest administrators a dedicated page for enabling the
animator, editing site medal bands and styles, and managing global or site
operator credentials.

## Source-plan coverage

This phase implements unified-plan section 4.4. It uses a dedicated page because
`web/template/admin/edit_metadata.html` is already large and must not absorb
another responsibility.

## Dependencies

Complete [Phase 02](Phase-02.md) first. The services must be stable before
routes and forms call them.

## Session scope

Limit the session to Web admin routes, templates, JavaScript or CSS when needed,
focused tests, and required route/service documentation.

## Required preflight

Complete these checks before editing code:

1. Read `web/routes/contest_admin_metadata.py`, the contest-admin context
   dependency, `web/template/admin/edit_metadata.html`, and the admin dashboard.
2. Read `docs/PADROES_UI.md`, `web/static/css/contest.css`,
   `shared/static/css/common.css`, and existing reusable Web scripts.
3. Search PyPI for a maintained admin-form package. Record why the existing
   FastAPI, Pydantic, Bootstrap, and vanilla-JavaScript stack is sufficient.
4. Confirm the existing CSRF and contest-admin authorization patterns and reuse
   them without creating a second authentication path.

## Route and UI contract

Create these focused routes under `/c/{slug}/admin/animator`:

- `GET /` renders animator settings and credential metadata.
- `POST /settings` updates `animator_enabled`.
- `POST /sites/{site_id}` updates one site's medal and style settings.
- `POST /sites/{site_id}/secrets` creates a site operator credential.
- `POST /secrets/global` creates a contest-global operator credential.
- `POST /secrets/{secret_id}/revoke` revokes either credential type.

The page and operations must provide these behaviors:

- View and update `animator_enabled`.
- Update each site's ordered medal cutoffs and optional style class.
- Generate a site-scoped operator credential.
- Generate a contest-global operator credential.
- Revoke either credential type.
- Show credential labels, scope, and creation date, but never the digest.
- Show new plaintext exactly once in the POST response or one-time flash view.

## Implementation tasks

Implement the page in this order:

1. Add a focused router such as `web/routes/contest_admin_animator.py` with a
   router-level admin dependency where current conventions permit it.
2. Use `Annotated` FastAPI parameters and one HTTP operation per function.
3. Add Pydantic input models for cutoff, style, and label validation.
4. Add `web/template/admin/animator_settings.html`. Use existing shared and Web
   CSS classes, no inline CSS, and no inline JavaScript.
5. If interaction requires new JavaScript, add a focused file under
   `web/static/js/` and reuse the existing copy-to-clipboard pattern.
6. Add an entry point from the contest admin dashboard.
7. Register the router in `web/main.py`.
8. Add route tests for authorization, successful updates, invalid cutoffs,
   credential one-time display, revocation, cross-contest IDs, and disabled
   state.
9. Update `web/docs/ROUTES.md`, `web/docs/URL_FOR_REFERENCE.md`, and
   `web/docs/SERVICES.md` if the route layer adds a service.

## Validation

Run focused Web checks and template formatting:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/web/test_contest_admin_animator.py tests/web/test_site_service.py -q
uv run djlint web/template/admin/animator_settings.html --check
UV_CACHE_DIR=/tmp/uv-cache uv run mypy web shared
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check web shared tests
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check web shared tests
```

Inspect the rendered response text to confirm neither `secret_digest` nor a
previously generated plaintext token is exposed.

## Completion criteria

This phase is complete when an authorized contest administrator can configure
animator access and site medal behavior, credential values are one-time only,
all new routes are documented, and the metadata page remains unchanged in size
and responsibility.

## Next phase

Continue with [Phase 04](Phase-04.md), which creates the standalone animator
runtime and its health endpoint.
