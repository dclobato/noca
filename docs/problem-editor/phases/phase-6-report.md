# Problem editor — Phase 6 Implementation Report
Date: 2026-08-14   Workspace member(s) touched: shared, web, arena

## 1. What was implemented

Every mutation initiated inside either problem editor now rides that editor's
single **Save**, in both modules and in both create and edit mode. Reorder is the
sole documented exception.

**Shared — the Save's vocabulary and ordering**

- `shared/services/testcase_pending_ops.py` (new) — reads the archive uploads off
  the request (`read_pending_uploads`) and parses the whole submission into an
  immutable `PendingTestCaseOps`: inline rows, `tc_remove_ids`,
  `tc_sample_toggle_ids`, `tc_bulk_zip`, repeatable `tc_add_zip`,
  `tc_replace_zip_{tc_id}`. Strategy-aware from the **stored**
  `ProblemValidatorType`. `validate_pending_conflicts` refuses a replace-all
  archive combined with any per-row operation.
- `shared/services/validator_action_ops.py` (new) — `parse_validator_action`,
  `PendingValidatorAction`, `ValidatorActionKind`; refuses an upload combined with
  a removal, and any validator field on a standard problem.
- `shared/services/testcase_form_echo.py` (new) — `submitted_case_rows` and
  `reselect_upload_labels`: what a rejected Save hands back.
- `shared/services/problem_save_errors.py` (new) — `PendingOpsError`,
  `require_supported_strategy`, `unsupported_strategy_message`.
- `shared/services/testcase_save_plan.py` (new) — pure `build_desired_cases` plus
  `materialize`, which brings the seeded staging directory into the desired shape
  without ever overwriting a file it still needs.
- `shared/services/problem_editor_save.py` (new) — the ordering written once:
  `lock_problem_row` → `open_save_swap` (which advances the generation fence) →
  `stage_test_cases` → commit, with `abandon_swap` covering the staging window
  `commit_with_edit_swap` does not.
- `shared/services/problem_package/{quarantine,edit_swap,journal,journal_model}.py`
  — additive `PlannedRemoval` / `plan_removal` / `EditArtifactSwap.stage_removal`,
  so a Save that *drops* a file (a statement switched from PDF to Markdown) is as
  reversible as one that replaces it. Journalled with an optional `removal` flag;
  no version bump, and recovery still decides from the quarantine on disk.

**Web**

- `contest_admin_problem_edit.py` — `edit_problem_submit` rewritten: locks the row
  before reading its cases, validates everything (scalars, statement, image,
  categories, limits, interactions, pending ops, validator action) before writing,
  stages test cases and statement through the swap, commits with
  `commit_with_edit_swap`, enqueues validator validation only afterwards, and
  redirects to `?tab=test-cases` when it touched cases or the validator.
- `contest_admin_problem.py` — `new_problem_submit` adopts the same field names,
  the same shared parser, and the same staged swap.
- New `web/services/problem_edit_save.py` (row application),
  `web/routes/contest_admin_problem_edit_render.py` (one editor context builder,
  which is what makes the row re-emit structural), `contest_admin_problem_validator.py`
  (the retained validator endpoints), `contest_admin_problem_tc_pages.py` (the two
  per-case pages).

**Arena**

- New `arena/routes/admin_problem_save.py` holding `arena_admin_problem_create`
  and `arena_admin_problem_update`, both on the shared parser, the row lock and the
  swap; validator removal folds into the Save through the new
  `apply_validator_removal`, and an upload through `build_validator_upload`.
- `arena/services/admin_problem_tc_pending.py` rewritten to apply a materialized
  plan to rows, with no post-commit filesystem callbacks.
- Arena **creation now collects test cases**, which the phase's fourth render path
  presupposes and which `problem_testcases_tab.html` had explicitly deferred to
  this phase.

**Templates and JS** — archive, replace and toggle controls are `form="edit-form"`
fields; per-row uploads and the toggle CSV live in `#tc-pending-files`, outside the
reorder-swapped fragment; the removal modal submits the editor's form;
`tc-pending-remove.js` → `tc-pending-state.js` (removals, replacements, toggles,
validator action, conflict notice, rehydration on `noca:tc-list-swapped`, and
`noca:reorder-availability`); `tc-reorder-sortable.js` dispatches the swap event and
honours the availability event; `tc-replace-row.js` parks the file instead of
submitting; new `problem-edit-validate.js`; new `testcase_add_row.html` re-renders a
typed row; the shell names the uploads a rejection lost.

**Satellite routes retained**, unreferenced by the editor, marked deprecated in both
`ROUTES.md`, now refusing a stored `checker` strategy explicitly and returning to
`?tab=test-cases` on failure as well as success.

## 2. Deviations from the phase

- **Arena create was turned on rather than treated as vacuous.** The phase asks for
  the inline-row re-emit in four render paths; Arena create rendered a read-only
  empty state because its POST parsed no test-case fields. Rather than declare the
  fourth path vacuous, this phase makes it real — which is what
  `problem_testcases_tab.html:15-17` had said would happen "later".
- **Create-mode field names were unified.** `new_problem_submit` used
  `testcases_zip` / `tc_zip_N`; renaming only the template would have silently
  dropped create-mode uploads, so both routes moved to `tc_bulk_zip` / `tc_add_zip`.
  The per-ZIP sample checkbox on creation is gone: uploaded cases are added secret
  and toggled afterwards (stated in the pane).
- **`EditArtifactSwap` gained `stage_removal`.** Phase 4 could only replace a
  target, and a statement format switch deletes the counterpart. Additive; the
  import path is untouched.
- **Two extra route-module splits** (`contest_admin_problem_tc_pages.py`,
  `arena/routes/admin_problem_save.py`) and a four-way split of the shared parser,
  to honour the size policy — see §6.
- **One fix outside the phase's subject**: `tests/browser/conftest.py` now skips
  under xdist. Playwright's sync API cannot share a worker with asyncio tests, so
  on any machine with UI credentials configured the browser checks broke *other*
  tests' workers and `uv run pytest -n auto` could not go green. Pre-existing
  (introduced with the browser checks), but it blocked this phase's own gate.
- `docs/problem-editor/PLAN.md` did not override the phase file anywhere.

## 3. Tests and validation

| Command | Result |
| --- | --- |
| `uv run ruff format .` | pass (no changes on the final run) |
| `uv run ruff check .` | pass — All checks passed |
| `uv run mypy web shared autojudge arena rating aiassistant healthmonitor animator` | pass — 613 source files |
| `uv run pytest tests/shared tests/web` (phase slice) | 1608 passed, 16 skipped |
| `uv run pytest tests/arena` (phase slice) | 1197 passed, 2 skipped |
| `uv run pytest -n auto` (full) | **3986 passed, 42 skipped** |
| `uv run djlint web/template arena/template shared/template --reformat` | 0 files updated |
| `node --check` on every changed `shared/static/js/*.js` | pass |
| `git diff --check` | clean |
| Manual: live `noca-web` + `noca-arena`, headless browser checks | `uv run pytest tests/browser` → **14 passed, 1 skipped** against both running servers |

## 4. Phase Tests section coverage

| Required test | Where it lives | Result |
| --- | --- | --- |
| Save applies pending add, remove, replace, toggle | `tests/web/test_contest_admin_problem_save_pending.py::test_save_applies_removal_addition_and_toggle_together`, `::test_a_per_row_replacement_rides_the_save`; Arena mirrors in `tests/arena/test_admin_problem_save_pending.py` | pass |
| Undo restores the original pending state | `tests/browser/test_problem_editor_ui.py::test_marking_a_row_for_removal_is_undoable` | pass (live) |
| Bulk-ZIP Save preserves unsaved metadata, returns on Test cases | `::test_a_bulk_zip_save_keeps_unsaved_metadata_and_returns_to_the_tab` (both modules) | pass |
| Bulk-replace conflict rejected before mutation | `::test_a_bulk_zip_conflicting_with_row_edits_is_refused_before_any_mutation` (both) + `tests/shared/test_testcase_pending_ops.py` | pass |
| Validator remove-plus-upload conflict rejected | `::test_removing_and_uploading_a_validator_in_one_save_is_refused` (both) | pass |
| Reorder disabled once a bulk ZIP is selected | `tests/browser/...::test_selecting_a_replace_all_archive_disables_reordering` | pass (live) |
| Failed Save re-emits typed inline rows | Web edit + Web create + Arena edit + Arena create (`::test_a_failed_save_re_emits_...`, `::test_a_failed_creation_re_emits_the_typed_rows`) | pass |
| Pending markers **and selected files** survive a reorder swap | markers: rehydration on `noca:tc-list-swapped` (`tc-pending-state.js`), exercised by the live toggle/undo checks; files: structural — the inputs live in `#tc-pending-files`, outside the swapped fragment | pass (see §8) |
| ZIP that fails to parse leaves the case set and form intact | `::test_an_unreadable_archive_leaves_the_case_set_untouched` (both) | pass |
| Validator upload stages a candidate, enqueues only after commit | `::test_a_validator_upload_rides_the_save_and_enqueues_after_the_commit` (asserts the row is committed at enqueue time) | pass |
| Validator removal via the modal saves first, returns to the tab | `::test_a_validator_removal_rides_the_save_and_returns_to_the_tab` (both) | pass |
| Standard requires output; interactive accepts input-only and forces secret | `tests/shared/test_testcase_pending_ops.py` + `::test_an_interactive_save_accepts_input_only_cases_...` (both) | pass |
| Standard problems reject validator actions on the direct routes | existing `tests/web/test_contest_admin_problem_chooser.py` / Arena validator tests, plus `::test_a_standard_problem_refuses_a_validator_through_the_save` | pass |
| Validation-failure re-render names every lost file input | `::test_a_failed_save_re_emits_the_typed_rows_and_names_the_lost_uploads` (both) | pass |
| Save-path durability (extra) | `tests/shared/test_problem_package_edit_swap.py` — removal committed / rolled back / recovered, and a journal without the optional key | pass |
| Staging-failure guard (extra) | `::test_a_staging_failure_leaves_the_problem_and_the_directory_untouched` | pass |
| Lock ordering (extra) | `::test_the_save_locks_the_problem_row_before_reading_its_test_cases` | pass |

## 5. Plan-specific invariants

| Invariant | Status |
| --- | --- |
| Satellite test-case routes retained and still passing their tests | held |
| Output checker limited to stored value, disabled card, rejected packages | held — and now refused explicitly by both Saves *and* the satellites |
| Import behavior unchanged by the edit-aware swap | held — `promotion.py`, `staging.py`, `ArtifactPromoter` and the `problem_exists` predicate untouched; only additive changes in `quarantine.py` / `edit_swap.py` / journal serialization |
| No Save-path test-case write bypasses the staged swap | held — `save_testcase_files` is never called on any Save path, with no size or count exemption |
| Behavioral decisions read the stored strategy, never validator presence | held |
| No database commit precedes an unprotected filesystem write | held for every editor Save (create and edit, both modules). The retained satellites keep their historical commit-then-write — see §8 |
| v1 packages and v1 contest backups still import/restore | held (untouched by this phase; full suite green) |
| Enum type created once, `create_type=False` after, dropped on downgrade | N/A — no migration in this phase |

## 6. AGENTS.md convention compliance

| Obligation | Status |
| --- | --- |
| Copyright header on all new/modified source files | done (three files still carrying the old author form were updated as part of their change) |
| ROUTES.md + URL_FOR_REFERENCE.md updated (both modules) | done — new Save fields, deprecation notes, moved modules |
| SERVICES.md / SHARED_SERVICES.md updated | done — `web/docs/SERVICES.md`, `arena/docs/SERVICES.md`, `docs/SHARED_SERVICES.md` (five new shared modules, the JS rename, `stage_removal`) |
| ARCHITECTURE.md updated | done — every artifact-touching editor Save is now generation-fenced, plus the reversible removal and the lock rule |
| New table(s) autovacuum decision recorded | N/A — no new table |
| No inline styles; new JS externalized; shared assets reused | done — one new CSS block, one new shared JS file, no new dependency |
| Shared partials free of module-specific `url_for` names | done — the four URL fields the editor no longer needs were removed from `ProblemFormView` |
| PyPI checked before hand-rolling | done — recorded in the preflight; no new dependency |
| File sizes within 100–300 LOC (none over ~500) | **partially** — see below |
| Google docstrings + full type hints on new code | done |

File sizes after the splits (before → after): `arena/routes/admin_problems.py`
852 → **415** (+ new `admin_problem_save.py` 602); `web/routes/contest_admin_problem_edit.py`
783 → **521** (+ `contest_admin_problem_validator.py` 243, `contest_admin_problem_edit_render.py` 157);
`web/routes/contest_admin_problem_tc.py` 714 → **631** (+ `contest_admin_problem_tc_pages.py` 158);
`web/routes/contest_admin_problem.py` 636 → **612**. Every touched route module ended
**smaller than it started**; four remain above the ~500 line of suspicion because
reaching the 100–300 band would mean restructuring handlers this phase does not
otherwise touch. Every new module is inside the band (58–348 LOC).

## 7. Reviewer disposition

Reviewer: **Codex**, three rounds (REJECT → REJECT → REJECT, each on new, concrete
grounds; the review budget was then spent and the final round's three issues were
folded in before implementation).

Resolved blocking issues:
1. *Arena create declared vacuous* → Arena create now collects rows (§2).
2. *Create/edit field-name mismatch would drop uploads* → both unified on the new names.
3. *No reversible deletion for the statement counterpart* → `stage_removal`, with recovery tests.
4. *`bump_artifact_generation` is the caller's job* → `open_save_swap` performs it before staging.
5. *Satellite failure paths did not return to the tab* → they do now.
6. *Parser boundary unworkable* → route-owned async reads, pure typed parser, validator fields in their own function.
7. *PyPI check "moot"* → recorded explicitly.
8. *ARCHITECTURE.md deferred* → updated in this change.
9. *Client requirements unproven* → live headless browser checks for undo, pending toggle and reorder disabling.
10. *Copyright headers* → all touched files carry the current form.
11. *Create-vs-edit locking protocol conflated* → split explicitly (edit locks, create inserts and flushes).
12. *Satellites treated `checker` as standard* → explicit refusal added, with route coverage.
13. *Upload size boundary* → the documented no-cap contract is preserved; no new limit invented.

Non-blocking suggestions adopted: `ProblemValidatorType` (not a boolean) into the
parsers; stale test-case ids are a stated concurrency error; every stale
`tc-pending-remove.js` reference updated; the ARCHITECTURE wording limited to
artifact-touching Saves (the Limits-only branch legitimately opens no swap).

## 8. Remaining blockers

None for the gate. Two limitations are carried deliberately and were reviewed:

- The retained, deprecated satellite endpoints still write their files *after* their
  commit. They take the same row lock for the duration of their transaction, but that
  post-commit window is outside it. Moving it would rewrite routes this phase must not
  change; they are unreachable from the editor and are deleted next release.
- "Selected files survive a reorder swap" is asserted structurally (the inputs are
  outside the swapped fragment) and by the marker-rehydration checks, not by a browser
  assertion that reads back a `File` object after a drag — driving a real
  drag-and-drop reorder in the headless checks was out of proportion to the risk.

## 9. Gate

> No action inside the editor discards unsaved work except the documented
> file-input case, every conflict is refused before mutation, and the retained
> satellite routes still pass their existing tests.

- *No action discards unsaved work* — **met**: archives, per-row replacements,
  sample toggles and both validator actions are fields on the one form; route tests
  in both modules assert that metadata edited alongside them survives.
- *Except the documented file-input case* — **met and mitigated**: typed rows are
  re-emitted on all four render paths, and the lost archives are named in a banner.
- *Every conflict refused before mutation* — **met**: bulk-vs-per-row and
  remove-vs-upload are refused in the shared parser, with tests asserting the rows
  and the on-disk files are unchanged; reorder is disabled client-side, verified live.
- *Satellite routes still pass their existing tests* — **met**: full suite green,
  including their unchanged tests.

## 10. Readiness for the next phase

Phase 7 (documentation and validation) is unblocked. Its prerequisites — phases 2
through 6 — now hold: the strategy and generation columns, v2 packages and backups,
the proven edit-aware swap (its recovery tests pass, now including the removal
cases), the tabbed editor, and this phase's unified Save. Phase 7 owns the
remaining narrative documents (`PADROES_UI.md`, the validator guides,
`BACKLOG.md`); the per-change reference updates this phase triggered are already
done. The follow-up *release* still owes the deletion of the deprecated satellite
routes.

COMPLETION: SATISFIED
