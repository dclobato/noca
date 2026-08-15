# Validator-specific tabbed problem editor — combined plan

This plan merges the Claude and Codex proposals for splitting the NOCA problem
editor by validator type. It introduces an explicit, immutable validation
strategy on every Contest and Arena problem, replaces each module's combined
creation page with a strategy chooser plus a validator-aware tabbed editor,
and routes every editor-originated mutation through a single **Save problem**
action that preserves pending work across tabs.

The active strategies are Standard (the built-in token comparator) and
Interactive (the concurrently-running custom validator). Output checker
appears in the chooser with accurate explanatory content but stays disabled
and cannot be selected through a crafted URL or a package. All facts below
were verified against the current codebase; where the two source plans
disagreed, the "Reconciliation decisions" section records the call and why.

## Reconciliation decisions

These are the contested points, resolved against the codebase.

1. **Immutability enforcement: service layer, no database trigger.** Codex
   made a `BEFORE UPDATE` trigger mandatory; Claude flagged it optional. The
   codebase has zero trigger precedent — no `CREATE TRIGGER` or
   `CREATE FUNCTION` anywhere in `migrations/`. Introducing the first trigger
   as a side effect of this change sets an unreviewed precedent, so
   immutability is enforced at the service boundary (both modules' update
   paths) and covered by tests. A trigger remains possible future hardening,
   deliberately out of scope here. **Stated boundary:** this makes the strategy
   unchangeable through every supported application workflow — forms, routes,
   services, packages, backups, and admin tooling — but not through direct SQL
   against the database. Anyone with a `psql` prompt can still change it, and
   nothing in this release detects that.
2. **Migration style: temporarily nullable, then `SET NOT NULL`.** Codex's
   approach. No `server_default` ever exists, which also satisfies Claude's
   concern that a retained default would silently classify insert paths that
   forget the field. Migration naming follows the verified
   `YYYYMMDDNNNN_snake.py` convention.
3. **Column type: `SAEnum(ProblemValidatorType, values_callable=...)`.** Both
   plans proposed `String(16)` + CHECK, but the verified convention for enum
   columns is `SAEnum` with `values_callable` (`shared/db_schema/problem.py:112`,
   `shared/db_schema/arena/arena_problems.py:198`), backed by native
   PostgreSQL enum types (the `ALTER TYPE ... ADD VALUE` precedent in
   `202606220007_add_new_arena_badges.py`). The native enum type itself
   provides the value-set restriction the CHECK was meant to add. Follow the
   convention instead.
4. **Reorder stays immediate, with state rehydration.** Claude's position,
   made concrete: `tc-reorder-sortable.js` performs a manual `fetch` +
   `outerHTML` swap (verified — it is not HTMX), so it grows a dispatched
   `noca:tc-list-swapped` event after the swap, and the pending-state script
   re-applies row markers from the hidden inputs. Per-row file inputs live
   outside the swapped fragment, so selected `File` objects survive. If
   rehydration proves unreliable during implementation, fall back to Codex's
   degraded behavior: disable reorder with an explanatory message while
   test-case operations are pending.
5. **View model: extend the existing convention, narrowly.** Claude proposed
   `shared/services/problem_form_view.py`; Codex warned against a broad
   form-view abstraction. The verified `TestCaseRowView` precedent
   (`shared/services/testcase_view.py`, routes pre-build URLs into a frozen
   dataclass consumed by a shared partial) is exactly the pattern the shared
   tab partials need, so a small view model in that style is justified — it
   eliminates concrete duplicated URL construction, which is Codex's own
   criterion for accepting the abstraction.
6. **Corrections from codebase verification.** `PlannedArtifact` lives in
   `shared/services/problem_package/staging.py`, not `promotion.py`.
   `profiling_limits.html` is Web-only
   (`web/template/admin/problems/profiling_limits.html`), so Web's Limits tab
   partial simply includes it and nothing is extracted to shared. Arena has
   no `admin_problem_tc_pending.py` route file; the retained inline-row parsers
   now live in shared services (`testcase_pending_ops.py` and
   `interaction_pending_ops.py`). Arena's problem form has no tabs
   and no `active_tab` today; Web's editor already has a two-tab
   Content/Limits strip with an `active_tab` round-trip
   (`web/routes/contest_admin_problem_edit.py:304`), and the unsaved-changes
   guard already skips `#active-tab-input`
   (`shared/static/js/problem-edit-unsaved-guard.js:20`), so tab switching
   never counts as an unsaved change — Codex's requirement is already
   satisfied by existing code.

## Phase 1 — Persist the validation strategy

This phase adds the durable discriminator, backfills it without changing
effective judging behavior, and removes behavioral inference from the
presence of validator source.

### Shared type and schema

Add the strategy vocabulary to `shared/enumerations.py`, next to
`CustomValidatorActiveState` (all enums in the file are `StrEnum`, and
lowercase values have precedent in `Environment` and `StatementLanguage`):

```python
class ProblemValidatorType(StrEnum):
    STANDARD = "standard"
    INTERACTIVE = "interactive"
    OUTPUT_CHECKER = "checker"
```

The Python name follows the docs' `STANDARD` / `INTERACTIVE` /
`OUTPUT_CHECKER` vocabulary while the wire value stays the lowercase
`checker` the package format specifies at
`docs/custom-validator/OUTPUT_CHECKER_VALIDATOR.md:613`.

Add a non-null `validator_type` column to both `problems`
(`shared/db_schema/problem.py`) and `arena_problems`
(`shared/db_schema/arena/arena_problems.py`), declared with the existing
`SAEnum(..., values_callable=lambda e: [m.value for m in e])` convention.

### Migration and backfill

Create one migration (`202608120001_problem_validator_type.py`, following the
verified naming convention) that:

1. Creates the PostgreSQL enum type **exactly once** —
   `postgresql.ENUM("standard", "interactive", "checker", name="problemvalidatortype").create(bind, checkfirst=True)`
   — then references it with `create_type=False` on both `add_column` calls.
   This is the verified precedent
   (`202606220001_add_arena_user_badges_table.py:48-54`); without it the second
   table's column emits a duplicate `CREATE TYPE` and the migration fails. The
   downgrade drops the columns and then the type with
   `.drop(bind, checkfirst=True)`. `checker` is in the type's value list from
   the start, so reserving it never needs a later `ALTER TYPE ... ADD VALUE`.
2. Adds both columns as temporarily nullable.
3. Backfills `interactive` for every problem with any custom-validator row,
   including rows holding only pending or invalid revisions:
   `UPDATE problems SET validator_type='interactive' WHERE id IN (SELECT
   problem_id FROM problem_custom_validators)`, plus the Arena mirror.
4. Backfills every other problem as `standard`.
5. Sets both columns `NOT NULL`.
6. Adds the **artifact generation** column described below.

### The artifact generation fence

Phase 5 needs to tell, after a crash, whether a Save's database transaction
committed. For an *import* the problem row's existence answers that. For an
*edit* it cannot: the problem exists either way. So add
`artifact_generation` (`BigInteger`, NOT NULL, default `0`) to `problems` and
`arena_problems`, incremented inside every Save transaction that promotes
filesystem artifacts. The promotion journal records the generation it expects
to see after commit; recovery compares the stored value against it, which is
the only reliable commit signal available to a process that crashed before
committing. See "Edit-aware artifact swap" in Phase 5.

No permanent database default: the strategy is stated explicitly at every
creation entry point. Neither table is new or high-churn, so no autovacuum
tuning migration is needed. Add a migration test in the style of the existing
`tests/web/test_contest_global_medals_migration.py` precedent.

### Immutability

Set `validator_type` only when creating or importing a problem
(`web/services/problem_service` create path,
`arena/services/admin_problem_service.create_problem`, and both package
importers). Beyond the browser interface:

- `arena/services/admin_problem_service.update_problem` and Web's
  `edit_problem_submit` raise if a caller supplies a value different from the
  stored one.
- Package import into an existing problem rejects a disagreeing
  `validator_type`.
- Edit pages render the strategy as a read-only badge; no editable strategy
  field exists on any edit form, and the create POST derives the strategy
  from the validated route parameter, not a hidden field.
- Validator upload, replacement, and removal never change the strategy: an
  Interactive problem whose source is removed stays Interactive and becomes
  non-judgeable until an active valid revision exists.

### Replace strategy inference

Today, interactive-ness is inferred as *"a `problem_custom_validators` row
exists"* — `status_view()` (`shared/services/custom_validator.py:210`) is
called 41 times across 13 files, and raw `custom_validator` presence checks
appear across Web, Arena, AutoJudge, and shared services. Switch every
*behavioral* decision to `problem.validator_type is
ProblemValidatorType.INTERACTIVE`, keeping `status_view()` only for the
compile-status badge. The audit must cover at least:

- Web and Arena problem create, edit, and test-case services and routes.
- Test-case ZIP parsing, storage, add/edit/download/export, including the
  `require_output=not interactive` decision (`shared/tc_zip.py:80`).
- Sample-interaction visibility and validation.
- Submission and solution-test eligibility gates on both modules.
- Arena problem enablement.
- AutoJudge dispatch (`autojudge/dispatch.py`, `submission_job.py`,
  `arena_submission_job.py`, `solution_test_job.py`) and test-case loading.
  This one needs a specific change rather than a call-site swap:
  `AutojudgeDb.get_custom_validator_dispatch_state`
  (`autojudge/db/_custom_validator.py:79`) currently selects **only** from the
  custom-validator table and derives `configured` from
  `active_source is not None or candidate_source is not None`. It must join the
  domain's problem table and return the explicit strategy, deciding:
  **Standard** → no interactive validator, regardless of any stale validator
  row; **Interactive** → require an active `VALID` revision and otherwise fail
  closed as non-judgeable; **Output checker** → fail as unsupported, never
  falling back to Standard.
- Problem-package import/export and Contest backup/restore.
- Public and administrative problem-detail indicators.
- Rejudge and problem-validation paths.

This is also a correctness fix: an Interactive problem whose validator is
still `PENDING` or `INVALID` must already enforce secret-only test cases,
which the current derivation does not do. Do not implement Output checker
dispatch; a stored `checker` value is reserved for forward compatibility and
stays non-judgeable in this release.

### Draft policy and the judgeability contract

Incomplete drafts are explicitly allowed — a first Save may have no test
cases and no validator source, while existing metadata and statement
validation still applies. This is what makes the "choose strategy, create,
then upload the validator" flow possible at all. Judgeability is enforced at
the gates, not at Save, with one shared contract (Codex's formulation):

- A **Standard** draft becomes judgeable when at least one test case exists
  and every test case has a present expected-output file (a present-but-empty
  file stays valid).
- An **Interactive** draft becomes judgeable when at least one secret
  input-only test case exists, an active `VALID` validator exists, and no
  test case carries Standard sample/output semantics.

Apply the same shared judgeability decision at every execution boundary:
Arena enablement, Contest and Arena submission creation, Web solution tests,
AutoJudge dispatch and preflight, administrative validation/status displays,
and full Interactive export completeness. Arena problems continue to start
disabled; Contest lifecycle permissions stay unchanged.

## Phase 2 — Adopt problem-package format version 2

`shared/services/problem_package/` is the single place format decisions are
made; keep it that way so Web and Arena cannot interpret packages
differently. All files named below were verified to exist.

### Metadata contract

- `constants.py`: bump `FORMAT_VERSION` from 1 to 2 (currently at line 26).
- `metadata.py::check_format_version`: accept a missing version and explicit
  `1` as legacy v1, plus explicit `2`; return the version.
- `model.py::PackageMetadata`: add `validator_type: ProblemValidatorType`
  beside `format_version` and `custom_validator` — that dataclass is the
  parsed `problem.json` contract and is where the discriminator belongs.
- `metadata.py::parse_metadata`: on **v1**, derive — `interactive` when
  `custom_validator` is present, else `standard`, never `checker`. On
  **v2**, require the discriminator and enforce what `problem.json` alone
  can see: `standard` requires `custom_validator: null`; `interactive`
  requires a non-null `custom_validator`; unknown values and inconsistent
  combinations fail before persistence. `checker` parses but import fails
  with an explicit "output checker validation is not available in this
  build" error — do not silently downgrade it, and do not import it as a
  disabled draft. That rejection is **centralized in the shared parser/reader**,
  which raises before any `ProblemPackage` is returned; the two domain importers
  never implement the check independently, so they cannot diverge on it.
- `reader.py::_read_validator` (line 164): it already raises when
  `validator/` members appear with no `custom_validator` declaration; extend
  that archive-index check so a `standard` declaration with `validator/`
  members is refused with the same clarity. Archive-member checks belong in
  the reader, not in metadata parsing, because only the reader holds the
  extracted archive index.
- `writer.py`: emit `format_version: 2` and `validator_type`.
  `checker_semantics` stays out of scope until the checker itself lands.
  Public exports remain non-importable and carry no strategy metadata or
  validator source.

### Import and export

Both importers (`web/services/problem_service/importing.py`,
`arena/services/admin_problem_io_service.py`) set the new problem's
`validator_type` from the normalized package metadata. Only v2 packages are
written: Standard full exports write `custom_validator: null`; Interactive
full exports require a complete source declaration. Refuse a full export of
an incomplete Interactive draft whose source is absent, with a clear
operator-facing error, rather than emitting a package that violates the v2
combination rules.

### Contest backups

Contest backups embed one standard problem package per problem
(`web/services/contest_backup_service/export.py`, via
`temporary_package_path`) plus JSON payload rows that include
`problem_custom_validators`. **The Contest backup format must be bumped to
version 2 in the same change** — this is not optional, and it is a stricter
requirement than it first appears:

- `validate_manifest`
  (`web/services/contest_backup_service/validation.py:167`) rejects anything
  whose `format_version` is not exactly the current constant. A new backup
  carrying `validator_type` while still labelled v1 is a breaking change
  mislabelled as compatible.
- `validate_row` (`row_validation.py:46`) validates each row against the live
  `Table`'s column set. Adding `validator_type` to `problems` therefore makes
  strict validation **expect** that column — so existing v1 archives stop
  restoring the moment the column lands, unless the v1 branch passes it through
  `optional_columns` and fills it by inference.

So: bump the backup `FORMAT_VERSION` to 2, have the restorer accept both 1 and
2, and apply the legacy strategy inference (custom validator present ⇒
`interactive`, otherwise `standard`, never `checker`) **only** on the v1 branch.
v2 archives carry the strategy explicitly in both the payload rows and their
embedded v2 problem packages. Add a regression test restoring a v1 archive
captured before this change. Do not compile restored validator data during
restore unless the existing restore contract already requires it.

Update in the same change: `docs/PROBLEM_PACKAGE_FORMAT.md`, the rendered
`shared/template/_partials/problem_package_format.html` reference shown on
both import pages, and the sample packages served by
`download_sample_problem_package` and `arena_admin_problem_sample_package`.

## Phase 3 — Build the strategy chooser

The existing **Add problem** entry point becomes a shared strategy selection
step, split from the creation form in both modules.

### Routes

Split each create flow into chooser and strategy-specific form routes,
keeping the existing form endpoint names to minimize `url_for` churn:

| Module | Chooser | Form |
| --- | --- | --- |
| Web | `GET /c/{slug}/admin/problems/new` → `new_problem_choose` | `GET\|POST /c/{slug}/admin/problems/new/{validator_type}` → `new_problem_form` / `new_problem_submit` |
| Arena | `GET /admin/problems/new` → `arena_admin_problem_new_choose` | `GET\|POST /admin/problems/new/{validator_type}` → `arena_admin_problem_new` / `arena_admin_problem_create` |

The routes enforce these rules:

- `{validator_type}` is validated against the enum. `standard` and
  `interactive` render the corresponding creation editor.
- `checker` redirects to the chooser with a "not available in this build"
  flash, so the disabled card cannot be bypassed by typing the URL; unknown
  values are rejected with 404. **Take the parameter as `str` and resolve it in
  the handler** — typing it as the enum makes FastAPI answer `422` before the
  handler runs, and `shared.error_handlers` renders that as a neutral JSON
  `{"error": …}` body, which is wrong for an HTML admin page and would also make
  the two outcomes indistinguishable.
- The strategy-specific POST derives the strategy from the validated route
  parameter, never from an editable hidden field, so form tampering cannot
  select or change a strategy.
- Arena's list-return context (`page`, `per_page`, `search`, `sort_by`,
  `owner_id`, `category_slugs`, `language`, and `next`) flows from the
  problem list through the chooser into the creation form, reusing the
  verified helpers in `arena/routes/admin_problem_form_views.py`
  (`return_state`, `problem_list_url`, `safe_next_path`).
- The **Import a problem** card links to the existing, unchanged import
  flows (`import_problem_form`, `arena_admin_problem_import_form`).

### Shared chooser partial

Create `shared/template/_partials/validator_choice_cards.html` — four large,
keyboard-accessible cards, parameterized by the three target URLs
(`standard_url`, `interactive_url`, `import_url`). Card copy is drawn from
`docs/custom-validator/`:

- **Standard** — "NOCA compares your program's output against a fixed
  expected output. Line-ending style, trailing whitespace and a missing
  final newline are forgiven. No validator program to write."
  (`TOKEN_VALIDATOR.md`)
- **Interactive** — "The problem is a conversation. You supply one source
  file that talks to the submission and decides the verdict with its exit
  code. Test cases carry input only and are always secret; contestants see
  sample interactions instead." (`INTERACTIVE_VALIDATOR.md`)
- **Output checker** — "For problems with many correct answers. The
  submission runs normally, then your checker inspects its output and
  decides whether it is valid." Rendered with `aria-disabled`, non-clickable
  and non-focusable as an action, with a **Coming soon** badge.
- **Import a problem** — links to the existing import flow.

Style with existing Bootstrap card utilities plus one new class block in
`shared/static/css/common.css` (`.noca-validator-choice-card`) — no inline
styles and no new frontend dependency.

## Phase 4 — Create the tabbed editor

The editor keeps one form and mounts every tab pane, so authors move between
sections without losing browser state.

### Form and navigation model

Retain the verified detached-form idiom documented at
`arena/template/admin/problem_form.html:8-15`: one empty
`<form id="edit-form">` owns every editable control through
`form="edit-form"`, all panes stay in the DOM, and Bootstrap only toggles
visibility — switching tabs cannot lose input. Nested immediate-operation
forms are removed from the editor (see Phase 5).

Use these canonical tab values: `metadata`, `statement`,
`sample-interactions`, `test-cases`, `limits`. Web extends its existing
two-tab Content/Limits strip (`web/template/admin/problems/edit.html:106`)
and its `active_tab` round-trip
(`web/routes/contest_admin_problem_edit.py:304`, valid values currently
`{"content", "limits"}`) to the shared set; Arena gains the same mechanism
(its existing tab precedent is `arena/template/admin/user_profile.html`).

Add a small shared controller, `shared/static/js/problem-edit-tabs.js`, that:

- Writes the hidden `active_tab` field on `shown.bs.tab`.
- Accepts and validates `?tab=`, preserving the active tab across
  server-side validation errors and redirects.
- Keeps standard Bootstrap `role="tablist"` keyboard semantics.
- Makes the tab row horizontally scrollable on narrow screens — five tabs
  will not fit a phone.

Tab switching must not count as an unsaved change; the existing guard
already skips `#active-tab-input`, so this holds by construction. Every
redirect back to the editor carries `?tab=…`, including the dedicated
test-case and interaction edit routes, which return to their tab **and** row
anchor (the existing `#tc-{id}` + `highlight-row.js` behavior).

### Sticky action bar

Place one sticky bar (`.noca-problem-save-bar` in
`shared/static/css/common.css`) above the tab strip, containing: one
**Save problem** submit with `form="edit-form"`, **Cancel**/**Back**, a
read-only strategy badge and label, and the existing edit-lock state where
relevant. The bar must not obscure focused content, alerts, or the module
navbar, and must work in both themes and responsive layouts.

The Contest running-contest rule is preserved: when `_is_edit_allowed` is
false but `_is_limits_edit_allowed` is true, the content tabs render locked
while the Limits tab still saves, and the sticky bar reflects which of the
two states applies rather than being unconditionally enabled.

### Tab ownership

Create shared partials under `shared/template/_partials/` so Web and Arena
cannot drift:

- `problem_statement_tab.html` — statement editor plus the existing
  `problem_image_field.html` illustration card (upload, preview, clear,
  caption). Parameterized `allow_pdf`: Web `true` (keeps the
  `statement_source` switch, PDF preview/download/replacement, filesystem
  handling); Arena `false` (database-backed Markdown only, with its language
  detection and syntax help). Do not remove Contest PDF support or add PDF
  storage to Arena.
- `problem_testcases_tab.html` — rejudge warning, bulk-ZIP input, the
  existing `testcase_list_table.html`, the add-rows block, **Add test case**
  and **Upload test case** buttons, and — when `interactive` — the validator
  source card (status, upload, replacement, removal).
- `problem_sample_interactions_tab.html` — thin wrapper over the existing
  `sample_interaction_card.html` (add, pending removal with undo, drag
  ordering, transcript-format help, explanation, maximum enforcement),
  rendered only for Interactive problems. Standard problems never render the
  tab or accept sample-interaction fields.

`testcase_list_table.html` (verified: 138 lines, parameters `rows`,
`is_edit_allowed`, `reorder_target`, `sample_toggle_disabled`) gains an
explicit `interactive` parameter that drops the **Output** header, cells,
sizes, and related instructions, forces every case to secret, and removes
sample toggles. `testcase_edit_form.html` already supports output omission;
update its callers to derive that flag from the stored strategy rather than
validator presence.

Per-module tabs, so each module keeps its own metadata surface:

- Web: `web/template/admin/problems/_tab_metadata.html` (title, balloon
  color, author, notes, Categories card) and `_tab_limits.html` (failover
  plus the existing Web-only `profiling_limits.html`).
- Arena: `arena/template/_partials/problem_tab_metadata.html` (title,
  source, author/owner, resource limits, notes, license, statement language,
  Categories card, edit-only difficulty history).

Both module templates shrink to header + save bar + tab strip + includes +
modals + scripts, bringing the 729-line `edit.html` and 572-line
`problem_form.html` back inside the 100–300-line guidance. To keep the
shared partials free of module-specific `url_for` names, add
`shared/services/problem_form_view.py`, a small frozen-dataclass view model
holding the pre-built tab URLs, following the verified `TestCaseRowView`
precedent (`shared/services/testcase_view.py`).

## Phase 5 — Fold every mutating action into Save

Today a ZIP upload, a per-row replace, or a sample toggle is its own POST
that redirects, silently discarding unsaved metadata and statement edits.
All of them become pending client state applied in the one Save transaction
— the model `tc_remove_ids` / `si_remove_ids` already use.

### Pending operations

New fields attached to `#edit-form`:

| Field | Replaces |
| --- | --- |
| `tc_bulk_zip` (file) | `upload_testcase_zip` / `arena_admin_problem_tc_zip_replace` |
| `tc_add_zip` (file, repeatable) | `add_test_case_zip` / `arena_admin_problem_tc_add_from_zip` |
| `tc_replace_zip_{tc_id}` (file) | `replace_test_case` / `arena_admin_problem_tc_replace` |
| `tc_sample_toggle_ids` (hidden CSV) | `toggle_test_case_sample` / `arena_admin_problem_tc_toggle_sample` |
| `validator_language_id`, `validator_source_file` | the nested validator upload form (Web create mode already does this) |

Per-row file inputs live in a hidden container **outside** the swapped table
fragment, keyed by test-case id, so they survive the reorder swap. Validator
removal also folds in: the removal modal's confirm submits the main form
with a validated action field (including the existing keep-or-destroy choice
for sample interactions) — the Save runs first, the confirmed removal
applies, and the redirect returns to `?tab=test-cases`. Removal never
changes `validator_type`.

Grow `shared/static/js/tc-pending-remove.js` into
**`tc-pending-state.js`**, owning removals, pending replaces, and pending
sample toggles. It renders each row's optimistic state with an undo,
dispatches the existing `noca:problem-edit-changed` event, and re-applies
visible pending state from the hidden inputs when the new
`noca:tc-list-swapped` event fires after a reorder swap.
`tc-replace-row.js` stops auto-submitting its own form and instead marks the
row pending.

### Conflicting operations

Reject conflicting combinations before any mutation, with a clear error —
never silently choose precedence:

- A bulk replace-all ZIP is mutually exclusive with inline additions, added
  single-case ZIPs, per-row removals, per-row replacements, and sample toggles
  in the same Save.
- Validator removal is mutually exclusive with uploading a replacement
  validator in the same Save.

**Reorder is enforced on the client, not in the Save route.** Reorder stays an
immediate POST (below), so there is no pending reorder for the Save route to
reject — a server-side "conflicts with pending reordering" rule would be
unenforceable. Instead `tc-pending-state.js` **disables reorder as soon as any
mutually exclusive pending operation exists** — in particular the moment a bulk
ZIP is selected — and shows why. The Save route still rejects the combinations
it *can* observe, listed above.

### Safe processing order

Process each Save in this order, extending the existing "validate before any
write" discipline in `arena/routes/admin_problems.py` and
`web/routes/contest_admin_problem_edit.py`:

1. Parse and validate scalar fields, statement, image, categories, limits,
   validator action, sample interactions, and all test-case operations.
2. Read uploaded content within the existing size limits.
3. Build the complete proposed database and filesystem outcome without
   mutating durable state.
4. Stage new statement, image, validator, and test-case files in
   request-private locations.
5. Apply strategy-specific invariants to the final proposed state.
6. Promote staged filesystem content, then apply ORM mutations in a single
   `session.commit()`.
7. If the commit fails, restore or remove promoted content through the
   registered rollback path.
8. Enqueue validator compilation only after the successful commit.
9. Redirect with HTTP 303 to the appropriate tab.

### Filesystem ordering

Today the ordinary test-case edit path commits the database and *then*
writes files (`pending_writes` in Web, `(cleanup_callbacks, file_writes)` in
Arena). That is survivable for one hand-edited case, but this change routes
whole archives — bulk replace-all, single-case ZIPs, batched per-row
replaces — through that same Save, and a write failing after the commit
would leave rows describing files that do not exist.

The import machinery is the right *shape* but cannot be reused as-is: every
one of its three moving parts assumes the problem is brand new, and each
assumption is false for an edit.

### Why the import machinery is unsafe for edits

- **Promotion destroys the previous content.** `ArtifactPromotion.promote()`
  (`shared/services/problem_package/staging.py:133`) calls
  `remove_path(artifact.target)` when the target already exists, then renames
  the staged directory in. `rollback()` then deletes the promoted target. On an
  import that is correct — nothing existed before. On an edit the sequence is:
  promote deletes the problem's existing test-case directory, the commit fails,
  rollback deletes the new one, and the problem is left with **no test-case
  files at all** while the database still describes the old ones. That is data
  loss on the recovery path, strictly worse than today's commit-then-write.
- **Recovery's commit signal is wrong.** `reconcile_journals`
  (`journal.py:192`) uses `problem_exists(domain, problem_id)` as its proxy for
  "did the transaction commit" — its docstring says so, and `ArtifactPromoter`'s
  repeats it. An edited problem exists either way, so recovery always takes the
  "names a committed problem; clearing it" branch: it deletes the journal
  without rolling anything back, leaving promoted files against un-updated rows,
  unrecoverably.
- **The API is package-shaped.** `ArtifactPromoter.stage()` takes a
  `ProblemPackage` and rebuilds the whole directory from `package.test_cases`.
  A Save has a delta, not a package.

### Edit-aware artifact swap

Add an edit-aware swap alongside the import path rather than bending the import
path to cover both:

1. **Quarantine, don't delete.** Promotion moves an existing target to a hidden
   sibling (the `hidden_sibling()` helper already produces same-filesystem
   staging names) instead of `remove_path`. Rollback restores the quarantined
   original; `finish()` deletes it only after the commit succeeds. This is the
   same displace-and-restore shape as `ContestFileQuarantine`, which
   `ArtifactPromotion`'s own docstring already cites as its model.
2. **Journal the quarantine.** Journal entries record the quarantined path
   beside `staged`/`target`/`root`, so startup reconciliation can *restore*,
   not merely delete.
3. **Fence on the generation, not on existence.** The journal records the
   `artifact_generation` value the Save expects the problem row to hold after
   commit (Phase 1). Recovery compares the stored generation: equal ⇒ the
   commit landed, keep the new artifacts and drop the quarantine; lower ⇒ the
   commit did not land, restore the quarantined originals. `problem_exists`
   stays the predicate for import journals only.

A Save materializes the complete desired test-case directory in staging, so a
Save touching one case still copies the problem's other cases into staging.
That cost is accepted: it buys a single same-filesystem rename at promotion
time and a reversible swap.

**No inline exception.** An earlier draft exempted "small inline single-case
edits" on the grounds that one file is atomically replaceable. That is wrong on
both counts: a Standard case is *two* files (`.in` and `.out`) plus database
metadata, and `save_testcase_files`
(`shared/services/testcase_files.py:57`) performs direct, non-atomic writes and
may additionally delete a stale `.out`. **Every test-case mutation performed by
the Save path uses the edit-aware staged swap**, with no size or count
exception. The retained satellite routes may keep their current
commit-then-write behavior until they are deleted; the new Save path may not.
Never commit database rows before unprotected filesystem writes.

ZIP semantics stay strategy-aware: Standard requires an output file per
case; Interactive accepts input-only archives
(`shared/tc_zip.py:parse_single_testcase_zip(..., require_output=False)`)
and never silently picks up an output member.

### The file-input failure mode

Browsers cannot repopulate `<input type="file">` after a failed validation,
so a Save that fails metadata validation discards every staged archive at
once. This is an accepted cost of the single-transaction model, mitigated
rather than designed away:

- On a validation-failure re-render, show a prominent alert naming each
  staged upload that must be re-selected (bulk ZIP, single-case ZIPs,
  per-row replaces), driven off the field names present in the rejected
  POST. All non-file input — text, pending toggles, pending removals, inline
  rows — must be preserved.

  **Correction (found in phase 5 usage testing).** An earlier draft said this
  input "is preserved as it is today". It is not: no render path echoes
  `tc_in_N` / `tc_out_N` / `tc_explanation_N` / `tc_is_sample_N` back, in either
  module, in create or edit mode. So *any* validation failure — a malformed
  sample-interaction transcript, an unreadable ZIP, a blank title — silently
  discards every inline test-case row the author typed. This predates the tabbed
  editor and is not a regression from it, but it is real data loss and phase 6
  owns the fix, since phase 6 is what routes these fields through one Save.
- Validate the cheap, common metadata failures client-side (required title,
  numeric limits) before submit, so the usual near-miss never reaches the
  server with files attached.
- Keep the pending-state markers rendered after the failed Save so the
  author can see exactly which rows still expect a file.
- A ZIP validation failure leaves the existing test-case set unchanged;
  prefer atomic whole-request failure so authors correct an invalid archive
  without a partially applied Save.

### Existing satellite routes

The immediate bulk, add, replace, and toggle routes stop being referenced by
the editor UI but are **retained** in this change: apply the new explicit
strategy checks to them, make them redirect to `?tab=test-cases`, mark them
deprecated in both `ROUTES.md` files, and delete them in a follow-up commit
once the pending model has shipped and proven out. Deleting them in the same
release enlarges it and removes the fallback if the new path has a bug.
Their existing tests keep them honest in the meantime.

**Reorder** (`move_test_case_route`, `arena_admin_problem_tc_move`,
`interaction_move`) is the one action that stays immediate: it is idempotent
and already a fragment swap. `tc-reorder-sortable.js` dispatches
`noca:tc-list-swapped` after its `outerHTML` replacement so pending markers are
re-applied. Rehydration is a *usability* mechanism only — it does not enforce
any conflict rule, which is why reorder is additionally disabled while
conflicting pending operations exist (above).

## Files most affected

The change touches these files, all verified to exist unless marked new:

- `shared/enumerations.py`, `shared/db_schema/problem.py`,
  `shared/db_schema/arena/arena_problems.py`, one new
  `migrations/versions/202608120001_problem_validator_type.py`.
- `shared/services/problem_package/{constants,metadata,model,reader,writer}.py`,
  plus `staging.py`, `promotion.py`, `journal.py`, and `reconcile.py` for the
  edit-aware swap.
- `shared/services/testcase_files.py` (staged-swap write path),
  `shared/services/custom_validator.py`, `shared/services/testcase_view.py`
  (the row view gains the `interactive` flag).
- `autojudge/db/_custom_validator.py` (strategy-aware dispatch state),
  `autojudge/dispatch.py`.
- `web/services/contest_backup_service/{export,validation,row_validation}.py`
  and its version constant.
- `shared/template/_partials/` — new `validator_choice_cards.html`,
  `problem_statement_tab.html`, `problem_testcases_tab.html`,
  `problem_sample_interactions_tab.html`; edited `testcase_list_table.html`.
- `shared/services/problem_form_view.py` (new),
  `shared/static/js/tc-pending-state.js` (from `tc-pending-remove.js`),
  `shared/static/js/problem-edit-tabs.js` (new),
  `shared/static/js/tc-reorder-sortable.js`, `tc-replace-row.js`,
  `shared/static/css/common.css`.
- Web: `routes/contest_admin_problem.py`, `contest_admin_problem_edit.py`,
  `contest_admin_problem_tc.py`, `contest_admin_problem_io.py`,
  `contest_admin_problem_interactions.py`;
  `template/admin/problems/edit.html` plus new `_tab_*.html` and a chooser
  template.
- Arena: `routes/admin_problems.py`, `admin_problem_form_views.py`,
  `admin_problem_tc.py`, `admin_problem_io.py`,
  `admin_problem_interaction.py`, `admin_problem_validator.py`;
  `services/admin_problem_io_service.py`; shared inline-row parsing lives in
  `shared/services/testcase_pending_ops.py` and
  `shared/services/interaction_pending_ops.py`;
  `template/admin/problem_form.html` plus new partials and a chooser
  template.

Keep new source files within the 100–300-line target, and update the
copyright header on every modified source file to the current required form.

## Documentation

Update every applicable document in the same change, per the repository's
documentation-impact rules:

- `docs/ARCHITECTURE.md` — explicit stored strategy, immutability, and
  judgeability, plus the edit-aware artifact swap beside the existing
  "prepare → promote → commit → erase the journal" import description, which
  now covers two recovery predicates (problem existence for imports, the
  `artifact_generation` fence for edits).
- `docs/DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md` — the judge dispatches on
  the stored strategy, not on validator presence, plus incomplete-draft
  gates.
- `docs/PROBLEM_PACKAGE_FORMAT.md` — version 2, version-1 compatibility, and
  strategy combinations.
- `docs/CONTEST_BACKUP_FORMAT.md` — **format version 2**, strategy in the
  payload, and v1 legacy normalization on restore.
- `docs/SHARED_SERVICES.md` — the package contract and the new form view
  model.
- `docs/PADROES_UI.md` — new "Problem editor" pattern: chooser, tabs, sticky
  Save, pending operations.
- `docs/custom-validator/INTERACTIVE_VALIDATOR.md` — immutability now
  enforced; `docs/custom-validator/OUTPUT_CHECKER_VALIDATOR.md` — mark the
  package-representation section adopted for the discriminator, pending for
  `checker_semantics`.
- `docs/BACKLOG.md` — mark strategy persistence, immutability, and the
  applicable package work complete; keep checker runtime,
  `checker_semantics`, and diagnostics pending.
- `web/docs/ROUTES.md` + `URL_FOR_REFERENCE.md` and the Arena pair — new
  chooser routes, changed create paths, deprecated satellite routes. Update
  route documentation only for routes actually added, changed, deprecated,
  or removed.
- `web/docs/SERVICES.md`, `arena/docs/SERVICES.md` — new and changed
  services.
- Shared import help and sample-package content.

## Test plan

The regression suite covers persistence, UX, package compatibility, file
coordination, and judging behavior.

### Migration and persistence

- Existing custom-validator rows (any state) backfill to Interactive;
  problems without them backfill to Standard.
- New rows require a valid strategy; invalid stored values fail.
- ORM and service attempts to change the strategy fail.
- Validator source removal leaves the problem Interactive and non-judgeable.

### Chooser and editor

For both modules:

- The chooser renders four cards with accurate copy; Standard, Interactive,
  and Import are actionable; Output checker is disabled and non-focusable.
- `/new/checker` and tampered strategy values are refused.
- The Standard editor has no validator card and no Sample interactions tab;
  the Interactive editor shows both and has no Output column, no output
  fields, and no sample toggles.
- Editing never offers a strategy change; the badge is read-only.
- Tab switching preserves values; `?tab=` round-trips across validation
  errors and redirects; dedicated edit routes return to their tab and row
  anchor.
- The running-contest lock renders content tabs read-only while Limits still
  saves.
- Sticky bar and tabs render accessibly at desktop and mobile widths.

### Drafts and judgeability

- A strategy-only draft saves with no cases and no validator source.
- Incomplete drafts are refused at submission, solution-test, Arena
  enablement, and full export; completing the data restores judgeability.
- A pending or invalid Interactive candidate keeps the problem Interactive.

### Test-case and validator operations

- Standard inline and ZIP cases require output; Interactive accepts input
  only and forces secret.
- Save applies pending add, remove, replace, and toggle operations; undo
  restores the original pending state.
- Bulk-replace conflicts and remove-plus-replace validator conflicts are
  rejected before mutation.
- A ZIP that fails to parse leaves the existing test-case set and the saved
  form intact.
- Pending markers and selected files survive a reorder swap.
- Validator upload stages a candidate and enqueues validation only after
  commit; replacement preserves the old active revision until promotion.
- A bulk-ZIP Save preserves unsaved metadata/statement edits and returns on
  the Test cases tab.
- Standard problems reject validator actions through direct routes.

### Package and backup

- Missing-version and v1 packages import with the legacy mapping.
- v2 Standard and Interactive packages round-trip across modules.
- v2 missing/invalid strategies and inconsistent source/member combinations
  fail before persistence; `checker` fails with the explicit unsupported
  message.
- Incomplete Interactive full export fails clearly.
- Contest backup/restore preserves the strategy; legacy backups normalize
  deterministically.
- A v1 archive captured **before** this change still restores after the
  `validator_type` column lands (the strict row validator must treat it as
  optional on the v1 branch); a v2 archive round-trips with the strategy
  explicit; an unknown backup version is still refused.

### AutoJudge and filesystem ordering

- Dispatch uses the explicit strategy; an Interactive problem whose source
  was removed is dispatched as Interactive and refused as non-judgeable —
  never silently judged with the token comparator.
- A Standard problem carrying a stale validator row dispatches as Standard.
- A stored `checker` strategy fails as unsupported and never falls back.
- **Edit-aware swap**: a forced commit failure after promotion restores the
  problem's previous test-case files exactly — the case that the import
  machinery would have left empty. A forced crash between promotion and commit
  is recovered at startup by comparing `artifact_generation`, restoring the
  quarantined originals; the same crash *after* a successful commit keeps the
  new artifacts and drops the quarantine.
- Import journals continue to reconcile on problem existence, unaffected by the
  generation fence.

## Verification

Run validation in bounded stages:

1. Focused migration, package, shared test-case, Web route, Arena route,
   submission, solution-test, and AutoJudge tests.
2. A JavaScript syntax pass over every changed `shared/static/js/` file, and
   per-file `djlint --check` while iterating on templates.
3. `uv run ruff format . && uv run ruff check --fix .`
4. `uv run mypy web shared autojudge arena rating aiassistant healthmonitor animator`
5. `uv run djlint web/template arena/template shared/template --reformat`
6. `uv run pytest -n auto` with a generous timeout (about 5 minutes).
7. `git diff --check`.
8. Manual end-to-end with `uv run noca-web` and `uv run noca-arena`: create
   one problem of each active strategy, edit across all tabs without saving,
   upload a ZIP, confirm nothing was lost and the Test cases tab is
   selected, then export and re-import the package in the other module. If
   live-browser validation is unavailable, report that limitation
   explicitly.

## Suggested commit sequence

1. `validator_type` and `artifact_generation` columns, enum type, migration,
   backfill, and immutability guards.
2. The inference replacement across Web, Arena, shared, and AutoJudge — its own
   commit, since it touches 41 call sites in 13 files plus dispatch, and a
   judging regression must be bisectable in isolation.
3. Package format v2, Contest backup format v2, and v1 compatibility on both.
4. The edit-aware artifact swap (quarantine, journal restore, generation
   fence) with its recovery tests, landed **before** anything depends on it.
5. Strategy chooser and split create routes.
6. Tabbed editor and shared tab partials.
7. Fold satellite actions into Save, on top of the swap from commit 4; routes
   retained but deprecated.
8. Documentation and regression coverage completion.
9. *Follow-up release:* delete the deprecated satellite routes and their
   documentation entries.

## Assumptions

These decisions are locked for implementation:

- Output checker runtime, persistence, semantics, and editor stay out of
  scope; its chooser card is disabled and its packages are rejected, not
  imported as disabled drafts.
- Contest keeps its existing Limits tab and running-contest permissions;
  Arena keeps limits inside Problem metadata.
- Contest retains PDF-or-Markdown statements; Arena remains Markdown-only.
- Incomplete drafts are permitted but cannot be judged, enabled, or fully
  exported while their strategy contract is incomplete.
- Existing test-case satellite routes remain temporarily available as
  compatibility interfaces.
- One Save governs every mutation initiated from the tabbed editor,
  including validator removal; reorder is the sole immediate exception.
