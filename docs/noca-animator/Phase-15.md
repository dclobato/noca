# Phase 15: Expose team metadata, avatar, and audio

This session completes the MVP team-data surface using existing `users` and
`users_media` data. JSON responses contain metadata and URLs only; binary media
continues to use cache-aware endpoints.

## Source-plan coverage

This phase implements unified-plan section 6.1. The photo endpoint already
exists from Phase 13 and must be reused, not duplicated.

## Dependencies

Complete [Phase 14](Phase-14.md) first. This phase extends the tested media and
presentation patterns already used by the photo modal.

## Session scope

Limit this session to the team collection feed, avatar and audio responses,
optional audio controls in the reveal UI, tests, and documentation.

## Required preflight

Complete these checks before editing code:

1. Read Phase 13's photo implementation, `web/routes/user_media.py`, and
   `UserMedia` cache-version behavior.
2. Inspect current audio-player markup and scripts in Web templates.
3. Check PyPI and the browser platform for media-player dependencies. Record why
   the native `<audio>` element is sufficient unless a specific accessibility
   requirement is unmet.
4. Define safe MIME, invalid-base64, cache, missing-media, contest, and
   site-scope behavior before coding.

## Public contract

Add these enabled-contest operations:

- `GET /animator/c/{slug}/teams` returns safe team presentation metadata and
  route URLs for the selected global or site scope.
- `GET /animator/c/{slug}/teams/{team_id}/avatar` returns a stored avatar or the
  checked-in animator placeholder.
- `GET /animator/c/{slug}/teams/{team_id}/audio` returns the optional audio clip
  or `404` when none exists.

## Implementation tasks

Implement the team-data surface in this order:

1. Create typed team response models with ID, username, full name, site ID and
   name, location, media-availability flags, and URLs. Never return base64 data.
2. Reuse the enabled-contest and ceremony-scope dependencies from public reveal
   routes.
3. Extract a focused media query/response helper shared by photo, avatar, and
   audio operations without combining HTTP operations into one function.
4. Serve stored avatars with `dta_foto`-based ETags and the static placeholder
   fallback.
5. Serve audio with its stored MIME type, `dta_audio`-based ETag, conditional
   `304`, public cache control, and `Accept-Ranges` only if range requests are
   implemented and tested correctly.
6. Reject teams outside the contest or selected site with the same not-found
   response.
7. Add an optional audio control to the reveal UI. Never autoplay merely because
   a team receives focus; require an operator or spectator gesture and respect
   browser autoplay policy.
8. Add tests for metadata filtering, URL generation, avatar fallback, audio
   success and absence, invalid media, MIME types, ETags, scope isolation, and
   disabled contests.
9. Update `animator/docs/ROUTES.md` and `animator/docs/SERVICES.md`.

## Validation

Run the focused media checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/animator/test_team_routes.py \
  tests/animator/test_team_media_routes.py -q
uv run djlint animator/template/reveleitor.html --check
UV_CACHE_DIR=/tmp/uv-cache uv run mypy animator shared
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check animator tests/animator
```

Manually verify a team with all media, a team with only a photo-derived avatar,
and a team with no `users_media` row.

## Completion criteria

This phase is complete when presentation clients can list scoped teams, photo
logic remains single-source, avatars always have a fallback, optional audio is
cache-correct, and no media blob appears inside JSON.

## Next phase

Continue with [Phase 16](Phase-16.md), which adds contest-specific presentation
profiles and their administration workflow.
