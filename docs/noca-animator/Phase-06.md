# Phase 06: Render the initial live scoreboard interface

This session creates the first usable animator presentation page. It loads the
metadata and snapshot feeds, renders standings and contest time, and establishes
the CSS and JavaScript component boundaries used by later live animation.

## Source-plan coverage

This phase implements unified-plan section 2.4. Reveal controls and streaming
remain out of scope.

## Dependencies

Complete [Phase 05](Phase-05.md) first so the UI consumes stable, tested JSON
contracts rather than embedding database state in templates.

## Session scope

Limit this session to the public page route, Jinja templates, animator CSS,
animator JavaScript, accessibility, and focused UI tests.

## Required preflight

Complete these checks before editing code:

1. Read `docs/PADROES_UI.md`, `shared/static/css/common.css`,
   `healthmonitor/template/_base.html`, and relevant scoreboard templates.
2. Inspect shared Bootstrap, icon, font, theme, and static-asset loading
   patterns.
3. Search PyPI and the current asset registry for a maintained table-animation
   or virtual-DOM dependency. Record why vanilla JavaScript is sufficient for
   the initial full-snapshot render.
4. Define projector targets for 16:9 displays, long team names, many problems,
   and reduced-motion users before writing CSS.

## Page contract

Add `GET /animator/c/{slug}/` for enabled contests. The page must render these
elements from the existing APIs:

- Contest title, state, and countdown or elapsed timer.
- Ranked team rows with solved count and penalty.
- One cell per ordered problem, including solved, attempts, pending, and
  first-balloon states.
- Empty and recoverable error states.

## Implementation tasks

Implement the interface in this order:

1. Extend `animator/template/_base.html` with shared fonts, Bootstrap assets,
   theme initialization, and semantic page landmarks.
2. Add `animator/template/animator.html` with stable DOM IDs and data
   attributes.
3. Build `animator/static/css/animator.css` from reusable shared styles. Use no
   inline styles and account for horizontal problem overflow without page-level
   horizontal scrolling.
4. Build `animator/static/js/animator.js` as small rendering functions for meta,
   timer, rows, and problem cells. Use `textContent`, not HTML injection, for
   server-provided labels.
5. Fetch `/meta` and `/snapshot` concurrently, render an accessible loading
   state, and expose a retry action after transient failures.
6. Respect `prefers-reduced-motion` from the first version.
7. Add template, route, and static-contract tests for asset loading, gate
   behavior, DOM hooks, escaping, empty contests, and long content.
8. Update `animator/docs/ROUTES.md`.

## Validation

Run focused backend and template checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/animator/test_public_routes.py \
  tests/animator/test_animator_template.py -q
uv run djlint animator/template/animator.html --check
uv run djlint animator/template/_base.html --check
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check animator tests/animator
```

Perform a manual browser pass at desktop 16:9 and a narrow viewport. Verify no
console errors, broken assets, unsafe HTML insertion, or page-level overflow.

## Completion criteria

This phase is complete when an enabled contest has a readable presentation page
that renders the authoritative snapshot, handles failure and empty states, uses
external CSS and JavaScript only, and remains usable with reduced motion.

## Next phase

Continue with [Phase 07](Phase-07.md), which adds the backend verdict-event and
SSE pipeline.
