# Phase 15: Expose team metadata and avatar

This session completes the MVP team-data surface using existing `users` and
`users_media` data. JSON responses contain metadata and URLs only; binary media
continues to use the cache-aware photo and audio endpoints from Phases 13 and
14.

## Status: optional (backlog), not on the required path

This phase is **deferred to the backlog** and isn't a prerequisite for any later
phase. The required sequence goes from [Phase 14](Phase-14.md) straight to
[Phase 18](Phase-18.md).

Phases 13 and 14 already deliver the end-to-end team-media experience the
projector needs, which leaves this phase without a consumer:

- The projector composes its own photo and audio URLs from the bases the
  ceremony page passes it (`data-photo-base` / `data-audio-base` in
  `animator/template/ceremony.html`, consumed by
  `animator/static/js/ceremony-modal.js`). It never asks for a team list.
- `GET /animator/c/{slug}/teams/{team_id}/photo` already resolves photo →
  avatar → placeholder, so a dedicated `/avatar` endpoint would publish a second
  URL for the same asset with a *weaker* fallback chain. Two routes that can
  disagree about one team's image are worse than one route.
- The reveal projection already carries each team's id, short name, full name,
  and site (`TeamRevealView` in `animator/models/reveal_session.py`).
- The team modal already treats absent audio as the normal case and hides its
  player, so the availability flags would only save a request, not prevent an
  error.
- `users.location` is the one genuinely new field, and no surface displays it.

Implement this phase only when a real client needs a team catalogue independent
of a running ceremony — for example a broadcast overlay, a public team page, or
a projector that pre-loads media over a slow venue link. Until then, the scope
below stands as the specification for that future work.

## Source-plan coverage

This phase implements the remaining unified-plan section 6.1 work. The photo
endpoint already exists from Phase 13, and the audio endpoint already exists
from Phase 14; both must be reused, not duplicated.

## Dependencies

Complete [Phase 14](Phase-14.md) first. This phase extends the tested media and
presentation patterns already used by the photo-and-audio modal.

## Session scope

Limit this session to the team collection feed, avatar responses, photo/audio
URL and availability metadata, tests, and documentation.

## Required preflight

Complete these checks before editing code:

1. Read Phase 13's photo implementation, Phase 14's audio implementation,
   `web/routes/user_media.py`, and `UserMedia` cache-version behavior.
2. Define safe avatar MIME, invalid-base64, cache, missing-media, contest, and
   site-scope behavior before coding.
3. Define how the team feed reports photo, avatar, and audio availability and
   builds scoped route URLs without loading or serializing media blobs.

## Public contract

Add these enabled-contest operations:

- `GET /animator/c/{slug}/teams` returns safe team presentation metadata and
  route URLs for the selected global or site scope.
- `GET /animator/c/{slug}/teams/{team_id}/avatar` returns a stored avatar or the
  checked-in animator placeholder.

Reuse, without duplicating, these existing operations:

- Phase 13's `GET /animator/c/{slug}/teams/{team_id}/photo`.
- Phase 14's `GET /animator/c/{slug}/teams/{team_id}/audio`.

## Implementation tasks

Implement the team-data surface in this order:

1. Create typed team response models with ID, username, full name, site ID and
   name, location, media-availability flags, and URLs. Never return base64 data.
2. Reuse the enabled-contest and ceremony-scope dependencies from public reveal
   routes.
3. Extend the focused team-media query and response helpers already shared by
   the Phase 13 photo and Phase 14 audio operations without combining distinct
   HTTP operations into one function.
4. Serve stored avatars with `dta_foto`-based ETags and the static placeholder
   fallback.
5. Build photo, avatar, and audio availability flags and scoped URLs without
   returning base64 data or issuing an extra request per team.
6. Reject teams outside the contest or selected site with the same not-found
   response.
7. Add tests for metadata filtering, URL generation, media-availability flags,
   avatar fallback, scope isolation, disabled contests, and reuse of the
   existing photo and audio route names.
8. Update `animator/docs/ROUTES.md` and `animator/docs/SERVICES.md`.

## Validation

Run the focused media checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/animator/test_team_routes.py \
  tests/animator/test_team_media_routes.py -q
uv run djlint animator/template/ceremony.html --check
UV_CACHE_DIR=/tmp/uv-cache uv run mypy animator shared
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check animator tests/animator
```

Manually verify a team with all media, a team with only a photo-derived avatar,
and a team with no `users_media` row.

## Completion criteria

This phase is complete when presentation clients can list scoped teams, photo
and audio logic remain single-source, avatars always have a fallback, the feed
advertises media availability and scoped URLs correctly, and no media blob
appears inside JSON.

## Next phase

[Phase 16](Phase-16.md), which adds contest-specific presentation profiles and
their administration workflow, is also optional and builds on this phase. If
you're following the required path, continue with [Phase 18](Phase-18.md)
instead.
