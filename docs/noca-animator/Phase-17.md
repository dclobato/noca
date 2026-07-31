# Phase 17: Add presentation-profile administration

This session lets a contest administrator create, edit, and clear the optional
team presentation profiles introduced in Phase 16. It doesn't change the public
team response contract.

## Status: optional (backlog), not on the required path

This phase is **deferred to the backlog** and isn't a prerequisite for any later
phase. The required sequence goes from [Phase 14](Phase-14.md) straight to
[Phase 18](Phase-18.md). It's the write path for [Phase 16](Phase-16.md), so it
can't precede that phase and inherits its deferral.

A separate `/profiles` administration area would also create a second place to
administer one team: `web/template/admin/users/edit.html` already manages the
team's name, site, location, photo, derived avatar, and audio clip. If
presentation fields are ever needed, add a **Presentation** section to that
existing screen instead of building a parallel workflow. A separate table can
still back those fields if operational identity and branding must stay apart.

## Source-plan coverage

This phase completes the operational write path for unified-plan sections 6.2
and 6.3.

## Dependencies

Complete [Phase 16](Phase-16.md) first so the profile schema, validation, merge
order, and service contract are already tested.

## Session scope

Limit this session to a focused Web service, contest-admin routes, templates,
external CSS or JavaScript when needed, tests, and Web documentation.

## Required preflight

Complete these checks before editing code:

1. Read the animator settings page, Web user-admin routes, contest-admin
   context, and current profile and color-input patterns.
2. Read `docs/PADROES_UI.md`, `web/static/css/contest.css`, and shared styles
   and scripts before adding assets.
3. Search PyPI for an admin-form library. Record why current FastAPI, Pydantic,
   Bootstrap, and vanilla-JavaScript capabilities are sufficient.
4. Decide whether a dedicated page or a small settings-page subsection keeps
   every touched source file within project size guidance.

## Route contract

Add these contest-admin operations under the animator administration prefix:

- `GET /profiles` lists contest teams and their effective profile values.
- `GET /profiles/{team_id}` renders one edit form.
- `POST /profiles/{team_id}` creates or updates one profile.
- `POST /profiles/{team_id}/clear` removes overrides and restores fallbacks.

## Implementation tasks

Implement the administration flow in this order:

1. Add a thin Web service over the shared schema with explicit contest and
   `RoleEnum.TEAM` validation.
2. Add one HTTP operation per route and use reusable `Annotated` dependencies.
3. Validate display names, institutions, short names, color, and supported
   `media_json` fields with Pydantic before mutation.
4. Render effective fallback values separately from stored overrides so an
   administrator can tell what clearing a field does.
5. Use a dedicated template when the existing animator settings page would grow
   beyond project guidance. Use no inline CSS or JavaScript.
6. Add a navigation link from animator settings.
7. Test authorization, create, update, clear, validation failure, non-team IDs,
   cross-contest IDs, fallback restoration, and safe rendering.
8. Update `web/docs/ROUTES.md`, `web/docs/URL_FOR_REFERENCE.md`, and
   `web/docs/SERVICES.md`.

## Validation

Run focused Web and template checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/web/test_contest_admin_animator_profiles.py \
  tests/animator/test_team_profile_service.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run mypy web shared animator
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check web shared animator tests
```

Run `djlint --check` separately on every new or changed template to avoid the
repository's multi-file multiprocessing issue.

## Completion criteria

This phase is complete when authorized administrators can manage optional
presentation overrides, clearing restores Phase 15 fallbacks, validation and
contest boundaries hold, and all new routes and services are documented.

## Next phase

Continue with [Phase 18](Phase-18.md), which packages and wires the animator for
production deployment and health monitoring.
