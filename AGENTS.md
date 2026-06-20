# Project Instructions

## Commands

- `uv run ruff format .`: format code
- `uv run ruff check --fix .`: lint code
- `uv run pytest`: run tests (full suite); use `uv run pytest tests/<module>` for one module's slice
- `uv run mypy web shared autojudge arena rating`: type check
- `uv run python scripts/fetch_assets.py`: after fresh install, fetch shared web/arena assets
- `uv run noca-web`: run web server
- `uv run noca-arena`: run arena server
- `uv run noca-autojudge`: run autojudge worker
- `uv run noca-rating`: run the Arena rating worker (single replica only)
- `uv run djlint web/template --reformat`: format HTML templates

NOTE: Full test suite takes over 5 minutes, so keep timeout above this value

The repo is a uv workspace with five members: `shared`, `web`, `arena`, `autojudge`, `rating`. Each
declares its own runtime deps in `<module>/pyproject.toml`. `uv sync --all-packages` installs
the full developer environment; `uv sync --package noca-<module> --frozen --no-dev` installs
just one module's slice (used by the per-module Docker images).
The workspace packages are configured as live editable installs, so console-script entry points
(`noca-autojudge`, `noca-arena`, `noca-web`, `noca-rating`) import source and templates directly from the
workspace after a normal `uv sync --all-packages`.

For development, do not use docker containers for web/arena layer and autojudge. Pgsql and Valkey are accessed via already running docker containers (credentials on .env), so no need to run those in development.

## Writing code

Everytime we need a date/time picker on a HTML template, we must use Flatpickr. Check how we do in the arena/templates/auth/login.html and on arena/template/classes/problem_set_manage.html

In Python 3.14, "except X, Y:" is correct. There is no need to do "except (X, Y):"Each time you write new code, verify it for errors. If you identify any issue, correct it immediately. Do not leave errors in the code, regardless of severity or origin. Use the commands above to validate and format the code before committing.

While writing code for frontend on web module (HTML, CSS or JavaScript), check for available styles in /web/static/css/contest.css (and styles shared with arena in /shared/static/css/common.css). Do not use inline styles. For JavaScript, check if any of the already available scripts can be reuse or repurposed (including shared scripts in /shared/static/js/). If a new script is required, no not store it inline in the HTML, but create a new file in /web/static/js/ and include it properly in the HTML template.

While writing code for frontend on arena module (HTML, CSS or JavaScript), check for available styles in /arena/static/css/arena.css (and styles shared with web in /shared/static/css/common.css). Do not use inline styles. For JavaScript, check if any of the already available scripts can be reuse or repurposed (including shared scripts in /shared/static/js/). If a new script is required, no not store it inline in the HTML, but create a new file in /arena/static/js/ and include it properly in the HTML template.

Before writing code, check PyPi for existing libraries that can be used to solve the problem at hand. Do not reinvent the wheel if a well-maintained library already exists for the functionality you need.

Everytime you create/update/remove a route, update both ROUTES.md and URL_FOR_REFERENCE.md on arena/docs or web/docs

Everytime you create/update/remove a service, update SERVICES.md on area/docs or web/docs, or docs/SHARED_SERVICES.md

Check if you change requires updating ARCHITECTURE.md or CONFIG.md, and update them if required/relevant (changes in architecture, on how the app works, or any new configuration variable for app modules)

All source file must have a copyright header like the sample below

```python
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
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

See @docs/ARCHITECTURE.md for detailed architecture and design patterns used in the application.
