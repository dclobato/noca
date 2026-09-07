# Project Instructions

## Commands

- `uv run ruff format .`: format code
- `uv run ruff check --fix .`: lint code
- `uv run pytest`: run tests (full suite); use `uv run pytest tests/<module>` for one module's slice
- `uv run pytest -n auto`: run tests in parallel via pytest-xdist (one Valkey logical DB per worker; supports up to 14 workers)
- `uv run mypy web shared autojudge arena rating aiassistant healthmonitor animator mailer`: type check
  (`uv run mypy .` now covers the same surface: `tests/`, `scripts/`, `sample_question/` and
  `migrations/` are excluded in `pyproject.toml`, so a crawl cannot disagree with CI)
- `uv run python scripts/fetch_assets.py`: after fresh install, fetch shared web/arena assets
- `uv run noca-web`: run web server
- `uv run noca-arena`: run arena server
- `uv run noca-autojudge`: run autojudge worker
- `uv run noca-rating`: run the Arena rating worker (single replica only)
- `uv run noca-aiassistant`: run the Arena AI review worker
- `uv run noca-mailer`: run the outbound-email worker (single replica only)
- `uv run noca-healthmonitor`: run the health monitor server
- `uv run noca-animator`: run the animator presentation server
- `uv run djlint web/template --reformat`: format HTML templates

NOTE: The full test suite takes over 14 minutes serially, so keep the timeout above this value
for `uv run pytest`; `uv run pytest -n auto` (pytest-xdist) finishes in around 5 minutes but
still needs a generous timeout.

The repo is a uv workspace with nine members: `shared`, `web`, `arena`, `autojudge`, `rating`,
`aiassistant`, `healthmonitor`, `animator`, `mailer`. Each
declares its own runtime deps in `<module>/pyproject.toml`. `uv sync --all-packages` installs
the full developer environment; `uv sync --package noca-<module> --frozen --no-dev` installs
just one module's slice (used by the per-module Docker images).
The workspace packages are configured as live editable installs, so console-script entry points
(`noca-autojudge`, `noca-arena`, `noca-web`, `noca-rating`, `noca-aiassistant`, `noca-mailer`, `noca-healthmonitor`,
`noca-animator`) import source and templates directly from the
workspace after a normal `uv sync --all-packages`.

For development, do not use docker containers for web/arena layer and autojudge. Pgsql and Valkey are accessed via already running docker containers (credentials on .env), so no need to run those in development.

## Writing code

Everytime we need a date/time picker on a HTML template, we must use Flatpickr. Check how we do in the arena/templates/auth/login.html and on arena/template/classes/problem_set_manage.html

In Python 3.14, "except X, Y:" is correct. There is no need to do "except (X, Y):"

Each time you write new code, verify it for errors. If you identify any issue, correct it immediately. Do not leave errors in the code, regardless of severity or origin. Use the commands above to validate and format the code before committing.

While writing code for frontend on web module (HTML, CSS or JavaScript), check for available styles in /web/static/css/contest.css (and styles shared with arena in /shared/static/css/common.css). Do not use inline styles. For JavaScript, check if any of the already available scripts can be reuse or repurposed (including shared scripts in /shared/static/js/). If a new script is required, no not store it inline in the HTML, but create a new file in /web/static/js/ and include it properly in the HTML template.

While writing code for frontend on arena module (HTML, CSS or JavaScript), check for available styles in /arena/static/css/arena.css (and styles shared with web in /shared/static/css/common.css). Do not use inline styles. For JavaScript, check if any of the already available scripts can be reuse or repurposed (including shared scripts in /shared/static/js/). If a new script is required, no not store it inline in the HTML, but create a new file in /arena/static/js/ and include it properly in the HTML template.

### Rendering Markdown (single pipeline — never reimplement)

There is exactly ONE Markdown rendering pipeline in NOCA:
`shared/static/js/noca-markdown.js`. It owns the whole sequence —
`prepareMarkdown()` → `marked.parse()` → `DOMPurify.sanitize()` → DOM injection
→ Mermaid → KaTeX → directives. **Never** write a new renderer, and never
open-code any part of that sequence (no calling `marked.parse()`,
`DOMPurify.sanitize()`, or `renderMathInElement()` from a page script).

To render Markdown anywhere, bind declaratively — that is the entire contract:

```html
<!-- external source: entity-escaped inside a text/plain script blob -->
<div class="noca-markdown" data-noca-markdown="statement-src"></div>
<script id="statement-src" type="text/plain">{{ value | e }}</script>

<!-- in place: the element already holds its own decoded source text -->
<div class="noca-markdown" data-noca-markdown>{{ value | e }}</div>
```

The `noca-markdown` class is simultaneously the JS binding hook, the directive
scope, and the CSS hook (`:is(.noca-markdown, .editor-preview)` in
`shared/static/css/common.css`). Carrying the class is what gets a surface its
table borders, cell padding, GFM column alignment, and heading/table spacing.
That host is `:is()`, not `:where()`, and the table rules name the `tr` and
`th`/`td` types explicitly: every editor page loads `easymde.min.css` after
`common.css`, and EasyMDE's own `.editor-preview table td` rule otherwise ties
these on specificity and wins on source order — which silently made
`::: table-border off` a no-op in the live preview.

Rules:

- Both `_base.html` files already load the module; pages extending them need
  only the container markup plus the vendor libs they use (`marked.min.js`,
  `purify.min.js`, and `katex.min.js` + `auto-render.min.js` + `katex.min.css`
  and/or `mermaid.tiny.min.js`). Standalone full-HTML pages must include
  `noca-markdown.js` themselves.
- Never add a container id to a selector list in CSS or JS. If you find yourself
  enumerating containers, you are recreating the drift this pipeline replaced —
  a surface silently lost KaTeX, table styling, and directives three separate
  times because three hand-maintained lists disagreed.
- Code that owns its own element (e.g. the EasyMDE preview) calls
  `window.NocaMarkdown.toHtml()` / `.enhance()` / `.render()` / `.renderAll()`
  instead of duplicating the pipeline.
- Rendered-Markdown styling belongs in `shared/static/css/common.css` under the
  shared selector, never in a per-module or per-page stylesheet.

See `docs/SHARED_SERVICES.md` for the full contract.

Before writing code, check PyPi for existing libraries that can be used to solve the problem at hand. Do not reinvent the wheel if a well-maintained library already exists for the functionality you need.

Everytime you create/update/remove a route, update both ROUTES.md and URL_FOR_REFERENCE.md on arena/docs or web/docs

Everytime you create/update/remove a service, update SERVICES.md on area/docs or web/docs, or docs/SHARED_SERVICES.md

Check if your change requires updating the architecture documentation, and update it if required/relevant (changes in architecture or in how the app works): `docs/ARCHITECTURE.md` for how modules relate, or the module's own `docs/ARCHITECTURE_<MODULE>.md` for how one module works.

A new, renamed, or removed configuration variable needs THREE edits in the same
change, because each answers a different question and no one of them implies the
others:

1. `docs/CONFIG.md` — what the variable means, its range, and its default.
2. The right environment template — `.env.<module>.full` when one module reads
   it, or the shared layer matching its exact set of readers (`common`,
   `database`, `http`, `webarena`, `email`, `storage`, `workers`, `aireview`).
   Never define the same variable in two templates: a shared value has exactly
   one home. If no existing layer matches the set of readers, add a layer to
   `env_layers.toml` and to every stack that needs it rather than duplicating.
3. `docker-compose.yml.sample` — only when the value must differ *because* the
   process runs in a container (a bind address, a Compose service name, an
   in-container mount path, a pinned port).

NEVER reference an application variable as `${...}` in the compose file. Compose
resolves interpolation from the shell and the project-root `.env`, never from an
`env_file:`, so `${NOCA_WEB_PORT:-8000}` beside `env_file: .env.web.full` yields
8000 whatever that file says — and then overrides the file's real value, since
`environment:` outranks every `env_file`. Nothing warns. Only variables owned by
`.env.compose.full` (which belongs in the project-root `.env`) may be
interpolated; `tests/test_env_layers.py` enforces this.

`docs/ENV_LAYERS.md` explains the layering; `tests/test_env_layers.py` enforces
it and will fail if a setting exists in code with no template that supplies it.

### Documentation impact requirements

For every change, review its documentation impact and update every applicable
document in the same change:

- Architecture changes must update `docs/ARCHITECTURE.md` (cross-module
  design) or the owning module's `docs/ARCHITECTURE_<MODULE>.md`
  (`WEB`, `ARENA`, `AUTOJUDGE`, `RATING`, `AIASSISTANT`, `MAILER`,
  `HEALTHMONITOR`, `ANIMATOR`, `LANDINGPAGE`, `SHARED`).
- AI review flow changes must update `docs/AIREVIEW_FLOW.md` and
  `docs/AIASSISTANT.md`.
- Shared-service changes must update `docs/SHARED_SERVICES.md`.
- Judge container Dockerfile or startup behavior changes must update
  `docs/CONTAINER_STARTUP_OPTIONS.md`.
- Submission lifecycle or autojudge flow changes must update
  `docs/DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md`.

These requirements overlap. A change must update every applicable document.
Do not make no-op documentation edits merely to satisfy this rule; ensure the
documentation accurately reflects the resulting behavior.

Everytime you create a new table, analyze its write pattern and decide whether it needs a custom per-table autovacuum tuning migration (like `migrations/versions/202607180003_tune_autovacuum.py`). High-churn tables — those with heavy INSERT/UPDATE (e.g. the submission/judging pipeline) or append-then-bulk-delete tables pruned by a retention/reaper loop — should get tightened `autovacuum_*` storage parameters instead of relying on the server-wide defaults. Low-churn/reference tables do not need it.

Every new source file must use the copyright header below. When modifying an
existing source file, update its copyright header to this form as part of the
same change. Do not modify otherwise unchanged files solely to replace their
existing copyright header.

```python
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
```

## Code style

- Follow PEP 8 for Python code.
- Use 4 spaces for indentation.
- Use descriptive variable and function names.
- Write docstrings for all functions and classes using the Google style.
- Code format and linter should be enforced using `ruff` and `mypy` for type checking (see #Commands above).
- HTML templates should be reformatted with `djlint` (see #Commands above).
- Limit lines to 100 characters.
- Use type hints for all function parameters and return types.
- Web layer uses HTML + CSS + Vanilla JS + Bootstrap, follow standard web development best practices.
- Detailed web UI patterns are defined in /docs/PADROES_UI.md, follow those patterns for consistency.
- Keep each source file with 100 to 300 lines of code.

## Why large source code files are discouraged

Large files break readability:

- They violate the Single Responsibility Principle
- They make it harder to understand behavior without scrolling/searching
- They increase coupling and the chance of unintended side effects
- They discourage refactoring ("too big to touch" problem)

So, we are aiming at, in KLOC terms:

- 0.1 to 0.3 KLOC → very healthy range
- 0.3 to 0.5KLOC → acceptable
- 0.5 KLOC (~500 lines) → upper bound before suspicion
- 1+ KLOC → usually a red flag
- 1.5+ KLOC → almost certainly doing too much and is unacceptable

Check for details at [clean code guidelines for source code](docs/CLEAN_CODE_SOURCE_CODE.md)

## Arena reusable components

### Admin List Page pattern

All Arena admin list pages (`/admin/problems`, `/admin/categories`, `/admin/affiliations`, `/admin/users`)
follow a standard layout defined in `docs/PADROES_UI.md` under **"Admin List Page"**. Key rules:

- Header: `d-flex flex-wrap gap-2 align-items-center justify-content-between mb-3` with `mb-0` on `<h1>`
- Add-new button: `btn btn-primary btn-sm` + `add` icon + "Add new \<entity\>" (when applicable)
- Search input wrapper: `flex-grow-1 arena-filter-search` CSS class (no inline styles)
- Filter button: `btn btn-secondary btn-sm` + `filter_list` icon + "Filter"
- Clear filters link: always present, conditional style based on `_filters_active`

Reference template: `arena/template/admin/problem_list.html`

### Rating history chart (`arena/static/js/arena-rating-history-chart.js`)

A modular ECharts line chart for rating evolution (smoothed, time X-axis, Y from 0, dataZoom with 25% default window). To embed it on any page:

1. Create a JSON endpoint returning `{"history": [{"ts": "<ISO8601>", "rating": <int>}, ...]}`.
2. Include the partial, passing `chart_id` (unique DOM id) and `data_url` (endpoint URL):

```jinja
{% with chart_id="my-chart", data_url=request.url_for("my_endpoint") %}
    {% include "users/_rating_history_chart.html" %}
{% endwith %}
```

3. Add the ECharts CDN and the chart script to the page's `extra_script` block (already present on `profile.html`; copy those two `<script>` tags to any new page that needs the chart).

No JS changes are needed when reusing for problems, affiliations, or other entities — only a new endpoint and the include above.

## The application

The application is a web-based platform for competitive programming, allowing users to solve coding problems, submit solutions,
and receive feedback. The backend is built with Python, while the frontend uses HTML, CSS, and JavaScript. The application
includes features such as user authentication, problem browsing, code submission, and real-time feedback on solution correctness.

See @docs/ARCHITECTURE.md for the system architecture and the links to each module's own architecture document (`docs/ARCHITECTURE_<MODULE>.md`).
