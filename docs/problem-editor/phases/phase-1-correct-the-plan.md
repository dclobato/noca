# Phase 1 — Correct the plan

**Status: complete.** `docs/problem-editor/PLAN.md` has been patched. This file records
what was wrong, what the corrections are, and the evidence, so the later phases
can be executed without re-deriving any of it.

No code changes belong to this phase.

## Why the plan needed correcting

The plan reached "authoritative" while still asserting three things the
codebase contradicts, one under-specified worker change, and one rule that
could not be enforced as written. Each was verified before being folded in.

## Corrections folded in

### 1. Edit-aware artifact journal (was blocking)

The plan reused `ArtifactPromoter` + `ImportJournal` for editor saves. All
three of that machinery's assumptions are false for an edit:

- `ArtifactPromotion.promote()` (`shared/services/problem_package/staging.py:133`)
  calls `remove_path(target)` when the target exists, and `rollback()` deletes
  the promoted target. On an edit: promote destroys the old test-case
  directory, a failed commit deletes the new one, and the problem ends with
  **no test-case files** while the rows still describe the old ones.
- `reconcile_journals` (`journal.py:192`) uses `problem_exists()` as its proxy
  for "did the transaction commit" — true for imports, meaningless for edits,
  where the problem exists either way. Recovery would clear the journal without
  rolling anything back.
- `ArtifactPromoter.stage()` is package-shaped; a Save has a delta.

**Correction:** a separate edit-aware swap — quarantine instead of delete,
quarantined paths recorded in the journal so recovery restores rather than
deletes, and an `artifact_generation` column as the commit fence. Owned by
Phase 4.

### 2. The "small inline edit" exception (was blocking)

The plan exempted single-case inline edits from staged writes, claiming one
atomically replaceable file. Wrong on both counts: a Standard case is `.in`
**and** `.out` plus database metadata, and `save_testcase_files`
(`shared/services/testcase_files.py:57`) performs direct non-atomic writes and
may delete a stale `.out`.

**Correction:** no exception. Every Save-path test-case mutation uses the
staged swap. Retained satellite routes may keep commit-then-write until they
are deleted.

### 3. Contest backup versioning (was high — and worse than first reported)

`validate_manifest` (`web/services/contest_backup_service/validation.py:167`)
rejects any `format_version` that is not exactly the current constant, so a new
archive carrying `validator_type` while labelled v1 is a breaking change
mislabelled as compatible. The second effect is sharper: `validate_row`
(`row_validation.py:46`) validates rows against the live `Table`, so adding the
column makes strict validation **expect** it — breaking restore of *existing*
v1 archives the moment the column lands, regardless of what new exports write.

**Correction:** bump the backup format to v2, accept v1 and v2 on restore,
apply legacy inference only on the v1 branch, and pass `validator_type` through
`optional_columns` there. Owned by Phase 3.

### 4. AutoJudge strategy loading (was medium)

`AutojudgeDb.get_custom_validator_dispatch_state`
(`autojudge/db/_custom_validator.py:79`) selects only from the custom-validator
table and derives `configured` from source presence, so a call-site swap alone
would leave dispatch inferring.

**Correction:** the helper joins the domain's problem table and returns the
explicit strategy — Standard ignores stale validator rows, Interactive requires
an active `VALID` revision or fails closed, Output checker fails as
unsupported and never falls back. Owned by Phase 2.

### 5. Reorder conflict (was medium)

The plan declared bulk replacement incompatible with "pending reordering", but
reorder stays an immediate POST, so no pending reorder exists for the Save
route to reject.

**Correction:** enforcement moves to the client — reorder is disabled as soon
as a conflicting pending operation exists, in particular once a bulk ZIP is
selected. Rehydration after a fragment swap is usability, not enforcement.
Owned by Phase 6.

### 6. Clarifications

- **Immutability boundary stated explicitly:** service-layer enforcement covers
  every supported workflow but not direct SQL, and nothing detects that.
- **Checker rejection centralized** in the shared parser/reader, which raises
  before returning a `ProblemPackage`, so the two domain importers cannot
  diverge.

### 7. Carried from the earlier review round

- One-time `CREATE TYPE` with `create_type=False` on the second table
  (precedent: `202606220001_add_arena_user_badges_table.py:48-54`), and
  `.drop(bind, checkfirst=True)` on downgrade.
- `{validator_type}` taken as `str` and resolved in the handler: typed as the
  enum, FastAPI answers `422` before the handler runs and
  `shared.error_handlers` renders a neutral JSON body — wrong for an HTML admin
  page.
- Missing files added to the affected list: Web
  `contest_admin_problem_interactions.py`; Arena `admin_problem_interaction.py`,
  `admin_problem_validator.py`; `shared/services/custom_validator.py`;
  `shared/services/testcase_view.py`.
- Commit sequence resequenced: the swap lands before the Save folding that
  depends on it, and the 41-call-site inference replacement is its own commit so
  a judging regression stays bisectable.

## Gate

Phase 2 starts only once the above is reflected in `docs/problem-editor/PLAN.md` — it is —
and the phase files in this directory exist. No further verification needed.
