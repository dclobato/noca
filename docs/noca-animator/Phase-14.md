# Phase 14: Build reveal projector and operator interfaces

This session delivers the ceremony experience: a spectator projection with
focus and medal bands, plus a controller panel for authenticated step, back,
reset, and jump commands.

## Source-plan coverage

This phase completes unified-plan section 5.5, including the team-photo modal
from section 5.5.1.

## Dependencies

Complete [Phase 13](Phase-13.md) first so both interfaces consume stable
control, state, SSE, and media APIs.

## Session scope

Limit this session to Jinja shells, external CSS and JavaScript, command and
projection interaction, accessibility, tests, and route documentation.

## Required preflight

Complete these checks before editing code:

1. Read `docs/PADROES_UI.md`, shared Bootstrap modal patterns, shared fonts and
   theme assets, and the live animator renderer.
2. Inspect existing confirmation, keyboard, focus, and copy-to-clipboard scripts
   before adding new JavaScript.
3. Search PyPI and the asset registry for a reveal-presentation dependency.
   Record why existing Bootstrap and vanilla JavaScript cover the interaction.
4. Define projector behavior for 16:9 screens, many teams, long names, medal
   boundaries, reduced motion, and a disconnected controller.

## Interface contract

Build these interfaces:

- `reveleitor.html` renders frozen standings, current focus, pending cells,
  medal bands, contest/site identity, and a team-photo modal.
- `control.html` prompts for an operator secret and sends it only through the
  bearer header. It never puts the secret in a URL, DOM data attribute, log, or
  persistent browser storage.

## Implementation tasks

Implement the interfaces in this order:

1. Add `animator/template/reveleitor.html` and `control.html` based on the
   shared animator base.
2. Add `animator/static/css/reveleitor.css` with projector-scale typography,
   medal bands, focus states, pending cells, and responsive overflow behavior.
3. Add `animator/static/js/reveleitor.js` to fetch initial state, consume reveal
   SSE, render authoritative projections, and reconnect safely.
4. Make team names buttons or links with `data-team-id`. Open one reusable
   Bootstrap modal whose image URL includes validated ceremony scope.
5. Show photo, avatar, or placeholder without broken-image chrome. Keep the
   image within the viewport and allow modal-body scrolling.
6. Add `animator/static/js/control.js` for secret entry, bearer-authenticated
   commands, in-flight button disabling, status feedback, and current-state
   rendering.
7. Require confirmation for reset. Add keyboard shortcuts only when they don't
   fire inside form controls and are documented on screen.
8. Keep the token in JavaScript memory only. On reload, require re-entry.
9. Add routes for the operator HTML shell without accepting a secret parameter.
10. Add template and route tests for external assets, accessible controls,
    modal hooks, secret absence from URLs/HTML, scope propagation, and reduced
    motion.
11. Update `animator/docs/ROUTES.md`.

## Validation

Run focused template and route checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/animator/test_reveal_templates.py \
  tests/animator/test_control_routes.py -q
uv run djlint animator/template/reveleitor.html --check
uv run djlint animator/template/control.html --check
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check animator tests/animator
```

Run a manual two-window ceremony with one projector and one controller. Verify
step, back, jump, reset, reconnect, medal movement, site identity, photo modal,
wrong secret, and reduced-motion behavior.

## Completion criteria

This phase is complete when an operator can run a global or site ceremony from a
separate controller, projector state recovers after reconnect, medal and focus
changes remain clear, the photo modal always has a fallback, and secrets remain
out of URLs and persistent browser state.

## Next phase

Continue with [Phase 15](Phase-15.md), which adds the remaining team feed,
avatar, and optional audio endpoints.
