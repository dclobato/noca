# Phase 5 — Add the chooser and the tabbed editor

Replace each module's combined creation page with a strategy chooser plus a
validator-aware tabbed editor, sharing statement, test-case, and
sample-interaction markup between Web and Arena.

**Satellite routes are preserved and still work.** This phase changes what the
editor *looks like*, not how its mutations are applied — that is Phase 6.

Authoritative detail: `docs/problem-editor/PLAN.md`, "Phase 3 — Build the strategy chooser"
and "Phase 4 — Create the tabbed editor".

## Chooser

### Routes

| Module | Chooser | Form |
| --- | --- | --- |
| Web | `GET /c/{slug}/admin/problems/new` → `new_problem_choose` | `GET\|POST /c/{slug}/admin/problems/new/{validator_type}` → `new_problem_form` / `new_problem_submit` |
| Arena | `GET /admin/problems/new` → `arena_admin_problem_new_choose` | `GET\|POST /admin/problems/new/{validator_type}` → `arena_admin_problem_new` / `arena_admin_problem_create` |

Existing form endpoint names are kept to minimize `url_for` churn.

- `standard` and `interactive` render the corresponding creation editor.
- `checker` redirects to the chooser with a "not available in this build" flash;
  unknown values 404.
- **Take `{validator_type}` as `str` and resolve it in the handler.** Typed as
  the enum, FastAPI answers `422` before the handler runs, and
  `shared.error_handlers` renders that as a neutral JSON `{"error": …}` body —
  wrong for an HTML admin page, and it would make the two outcomes
  indistinguishable.
- The strategy-specific POST derives the strategy from the validated route
  parameter, never from an editable hidden field, so form tampering cannot
  select or change one.
- Arena's list-return context (`page`, `per_page`, `search`, `sort_by`,
  `owner_id`, `category_slugs`, `language`, `next`) flows from the list through
  the chooser into the form, reusing the verified helpers in
  `arena/routes/admin_problem_form_views.py` (`return_state`,
  `problem_list_url`, `safe_next_path`).
- **Import a problem** links to the existing, unchanged import flows.

### Shared partial

`shared/template/_partials/validator_choice_cards.html` — four large,
keyboard-accessible cards parameterized by `standard_url`, `interactive_url`,
`import_url`. Copy drawn from `docs/custom-validator/`:

- **Standard** — "NOCA compares your program's output against a fixed expected
  output. Line-ending style, trailing whitespace and a missing final newline are
  forgiven. No validator program to write."
- **Interactive** — "The problem is a conversation. You supply one source file
  that talks to the submission and decides the verdict with its exit code. Test
  cases carry input only and are always secret; contestants see sample
  interactions instead."
- **Output checker** — "For problems with many correct answers. The submission
  runs normally, then your checker inspects its output and decides whether it is
  valid." `aria-disabled`, non-clickable, non-focusable as an action, **Coming
  soon** badge.
- **Import a problem** — existing flow.

Style with Bootstrap card utilities plus one new `.noca-validator-choice-card`
block in `shared/static/css/common.css`. No inline styles, no new frontend
dependency.

## Tabbed editor

### Form and navigation

Retain the verified detached-form idiom
(`arena/template/admin/problem_form.html:8-15`): one empty
`<form id="edit-form">` owns every control through `form="edit-form"`, all panes
stay in the DOM, Bootstrap only toggles visibility — switching tabs cannot lose
input.

Canonical tab values: `metadata`, `statement`, `sample-interactions`,
`test-cases`, `limits`. Web extends its existing two-tab strip
(`web/template/admin/problems/edit.html:106`) and `active_tab` round-trip
(`web/routes/contest_admin_problem_edit.py:304`, currently
`{"content", "limits"}`); Arena gains the same mechanism (its tab precedent is
`arena/template/admin/user_profile.html`).

New `shared/static/js/problem-edit-tabs.js`:

- writes hidden `active_tab` on `shown.bs.tab`;
- accepts and validates `?tab=`, preserving the active tab across validation
  errors and redirects;
- keeps standard Bootstrap `role="tablist"` keyboard semantics;
- makes the tab row horizontally scrollable on narrow screens.

Tab switching must not count as an unsaved change — the existing guard already
skips `#active-tab-input`
(`shared/static/js/problem-edit-unsaved-guard.js:20`), so this holds by
construction. Every redirect back to the editor carries `?tab=…`, including the
dedicated test-case and interaction edit routes, which return to their tab
**and** row anchor (existing `#tc-{id}` + `highlight-row.js`).

### Sticky action bar

One sticky bar (`.noca-problem-save-bar` in `shared/static/css/common.css`)
above the tab strip: **Save problem** (`form="edit-form"`), **Cancel**/**Back**,
a read-only strategy badge, and the existing edit-lock state. It must not
obscure focused content, alerts, or the navbar, and must work in both themes and
all responsive layouts.

Contest's running-contest rule is preserved: when `_is_edit_allowed` is false but
`_is_limits_edit_allowed` is true, content tabs render locked while Limits still
saves, and the bar reflects which state applies.

### Tab ownership

Shared, under `shared/template/_partials/`:

- `problem_statement_tab.html` — statement editor plus the existing
  `problem_image_field.html` illustration card. Parameterized `allow_pdf`: Web
  `true` (keeps `statement_source`, PDF preview/download/replacement, filesystem
  handling); Arena `false` (database-backed Markdown, language detection, syntax
  help). Do not remove Contest PDF support or add PDF storage to Arena.
- `problem_testcases_tab.html` — rejudge warning, bulk-ZIP input, the existing
  `testcase_list_table.html`, add-rows block, **Add test case** / **Upload test
  case**, and — when `interactive` — the validator source card.
- `problem_sample_interactions_tab.html` — thin wrapper over the existing
  `sample_interaction_card.html`, rendered only for Interactive problems.
  Standard problems never render the tab or accept sample-interaction fields.

`testcase_list_table.html` (138 lines; parameters `rows`, `is_edit_allowed`,
`reorder_target`, `sample_toggle_disabled`) gains an explicit `interactive`
parameter that drops the **Output** header, cells, sizes, and related
instructions, forces every case secret, and removes sample toggles.
`testcase_edit_form.html` already supports output omission — update its callers
to derive the flag from the stored strategy.

Per-module:

- Web `template/admin/problems/_tab_metadata.html` (title, balloon color,
  author, notes, Categories) and `_tab_limits.html` (failover plus the Web-only
  `profiling_limits.html`).
- Arena `template/_partials/problem_tab_metadata.html` (title, source,
  author/owner, resource limits, notes, license, statement language, Categories,
  edit-only difficulty history).

Both module templates shrink to header + save bar + tab strip + includes +
modals + scripts, bringing the 729-line `edit.html` and 572-line
`problem_form.html` back inside the 100–300-line guidance. Add
`shared/services/problem_form_view.py`, a small frozen-dataclass view model
holding pre-built tab URLs, following the verified `TestCaseRowView` precedent
(`shared/services/testcase_view.py`), so shared partials never resolve
module-specific `url_for` names.

### Draft creation

A first Save may have no test cases and no validator source. Existing metadata
and statement validation still applies. Judgeability stays at the gates
(Phase 2).

## Tests

For both modules:

- The chooser renders four cards with accurate copy; Standard, Interactive, and
  Import are actionable; Output checker is disabled and non-focusable.
- `/new/checker` redirects with a flash; unknown values 404 (not 422).
- Tampered strategy values are refused.
- The Standard editor has no validator card and no Sample interactions tab; the
  Interactive editor shows both, with no Output column, no output fields, and no
  sample toggles.
- Editing never offers a strategy change; the badge is read-only.
- Tab switching preserves values; `?tab=` round-trips across validation errors
  and redirects; dedicated edit routes return to their tab and row anchor.
- The running-contest lock renders content tabs read-only while Limits saves.
- A strategy-only draft saves with no cases and no validator source.
- Sticky bar and tabs render accessibly at desktop and mobile widths.

## Gate

Both editors are tabbed and strategy-aware, creation goes through the chooser,
and every satellite route still works exactly as before.
