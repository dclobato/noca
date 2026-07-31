# Phase 14: Build reveal projector and operator interfaces

This session delivers the ceremony experience: a spectator projection with
focus and medal bands, a team modal with photo and optional audio playback, plus
a controller panel for authenticated step, back, reset, and jump commands.

## Source-plan coverage

This phase completes unified-plan section 5.5, including the team-photo modal
from section 5.5.1 and its optional team-audio playback.

## Dependencies

Complete [Phase 13](Phase-13.md) first so both interfaces consume stable
control, state, SSE, and photo APIs. Phase 14 adds the missing scoped audio
endpoint needed by the team modal.

## Session scope

Limit this session to the scoped team-audio response, Jinja shells, external CSS
and JavaScript, command and projection interaction, modal media playback,
accessibility, tests, and route and service documentation.

## Required preflight

Complete these checks before editing code:

1. Read `docs/PADROES_UI.md`, shared Bootstrap modal patterns, shared fonts and
   theme assets, and the live animator renderer.
2. Inspect existing confirmation, keyboard, focus, and copy-to-clipboard scripts
   before adding new JavaScript.
3. Read Phase 13's team-photo service, `web/routes/user_media.py`, the existing
   audio upload validation, and `users_media` audio cache-version behavior
   without importing Web implementations into Animator.
4. Verify browser autoplay behavior after a user-initiated Bootstrap modal open.
   Define an accessible fallback for a rejected `HTMLMediaElement.play()`
   promise.
5. Search PyPI and the asset registry for reveal-presentation and media-player
   dependencies. Record why existing Bootstrap, vanilla JavaScript, and the
   native `<audio>` element cover the interaction.
6. Define projector behavior for 16:9 screens, many teams, long names, medal
   boundaries, reduced motion, a disconnected controller, missing audio, and a
   browser that blocks autoplay.

## Interface contract

Build these interfaces:

- `ceremony.html` renders frozen standings, current focus, pending cells,
  medal bands, contest/site identity, and one reusable team modal. The modal
  displays the team's photo and attempts to autoplay its optional audio clip
  after a team-name click.
- `control.html` prompts for an operator secret and sends it only through the
  bearer header. It never puts the secret in a URL, DOM data attribute, log, or
  persistent browser storage.

Add this enabled-contest operation:

- `GET /animator/c/{slug}/teams/{team_id}/audio` returns the team's optional
  audio clip for the validated global or site scope, or `404` when the team is
  out of scope or has no usable clip.

## Implementation tasks

Implement the interfaces in this order:

1. Extend the Phase 13 team-media query and record with `audio_base64`,
   `audio_mime`, and `dta_audio`; keep the contest, `RoleEnum.TEAM`, and
   selected-site predicates in the same scoped lookup used by the photo route.
2. Add the scoped team-audio route. Decode base64 defensively, reject empty or
   unrecognized content, serve a canonical supported MIME type, return `404`
   when no usable clip exists, and never return audio data in JSON. Use an
   `audio` plus `dta_audio` ETag, honor weak/list/wildcard `If-None-Match`, and
   return public cache control on both `200` and `304`. Don't advertise byte
   ranges unless range requests are implemented and tested.
3. Add `animator/template/ceremony.html` and `control.html` based on the
   shared animator base.
4. Add `animator/static/css/ceremony.css` with projector-scale typography,
   medal bands, focus states, pending cells, and responsive overflow behavior.
   Use the shared medal assets at
   `shared/services/assets/{gold,silver,bronze}.svg` through the Animator
   `/assets/medal/{band}` route.
5. Add `animator/static/js/ceremony.js` to fetch initial state, consume reveal
   SSE, render authoritative projections, and reconnect safely.
6. Make team names buttons or links with `data-team-id`. Open one reusable
   Bootstrap modal whose photo and audio URLs include the validated ceremony
   scope.
7. Show the photo, avatar, or placeholder without broken-image chrome. Keep the
   image within the viewport and allow modal-body scrolling.
8. On `shown.bs.modal`, after the user clicks a team name, reset the reusable
   audio element to the selected team's scoped URL and call `play()`. Keep
   accessible native controls available if autoplay is rejected. A missing or
   invalid clip must leave the photo modal usable without a broken-player error.
9. On `hide.bs.modal`, pause playback, reset `currentTime`, remove the audio
   source, and reload the element so closing or switching the modal stops both
   playback and any in-flight media download. Make cleanup idempotent and repeat
   it on `hidden.bs.modal` as a teardown backstop.
10. Add `animator/static/js/control.js` for secret entry, bearer-authenticated
   commands, in-flight button disabling, status feedback, and current-state
   rendering.
11. Require confirmation for reset. Add keyboard shortcuts only when they don't
   fire inside form controls and are documented on screen.
12. Keep the token in JavaScript memory only. On reload, require re-entry.
13. Add routes for the operator HTML shell without accepting a secret parameter.
14. Add template, route, and JavaScript tests for external assets, accessible
    controls, modal hooks, secret absence from URLs/HTML, scope propagation,
    reduced motion, audio MIME and cache behavior, autoplay after modal display,
    rejected autoplay, missing audio, and stop/reset/source cleanup on close.
15. Update `animator/docs/ROUTES.md` and `animator/docs/SERVICES.md`.

## Validation

Run focused template and route checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/animator/test_reveal_templates.py \
  tests/animator/test_ceremony_js.py \
  tests/animator/test_team_audio_route.py \
  tests/animator/test_control_routes.py -q
uv run djlint animator/template/ceremony.html --check
uv run djlint animator/template/control.html --check
UV_CACHE_DIR=/tmp/uv-cache uv run mypy animator shared
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check animator tests/animator
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check animator tests/animator
```

Run a manual two-window ceremony with one projector and one controller. Verify
step, back, jump, reset, reconnect, medal movement, site identity, photo modal,
audio autoplay after a team-name click, graceful behavior when audio is absent
or autoplay is blocked, immediate playback stop on modal close, wrong secret,
and reduced-motion behavior.

## Completion criteria

This phase is complete when an operator can run a global or site ceremony from a
separate controller, projector state recovers after reconnect, medal and focus
changes remain clear, the photo modal always has a fallback, an available audio
clip starts when the user opens that team's modal and stops when the modal
closes, missing or blocked audio doesn't break the modal, and secrets remain out
of URLs and persistent browser state.

## Next phase

Continue with [Phase 18](Phase-18.md), which packages and wires the animator for
production deployment and health monitoring.

Phases [15](Phase-15.md), [16](Phase-16.md), and [17](Phase-17.md) — the
standalone team feed, the presentation-profile table, and its administration
screens — are optional backlog items. Phases 13 and 14 already give the
projector everything it needs for team photos and audio, so none of the three is
a prerequisite for Phase 18. Each phase document explains what would justify
picking it up.
