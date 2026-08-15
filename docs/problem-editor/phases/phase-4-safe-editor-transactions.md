# Phase 4 — Build safe editor transactions

Implement an edit-aware staged artifact swap that preserves existing files and
recovers correctly from a crash, and prove it under every failure point.

**This lands before any ZIP upload is connected to the main Save (Phase 6).**
Folding archives into a Save that still commits before writing files is the one
sequencing mistake that destroys author data.

Authoritative detail: `docs/problem-editor/PLAN.md`, "Filesystem ordering" and "Edit-aware
artifact swap".

## Why the import machinery cannot be reused

Each of its three parts assumes a brand-new problem:

- **Promotion destroys the previous content.** `ArtifactPromotion.promote()`
  (`shared/services/problem_package/staging.py:133`) calls
  `remove_path(artifact.target)` when the target exists, then renames the staged
  directory in; `rollback()` deletes the promoted target. On an edit: promote
  deletes the existing test-case directory, the commit fails, rollback deletes
  the new one, and the problem is left with **no test-case files at all** while
  the rows still describe the old ones. Strictly worse than today's
  commit-then-write.
- **The commit signal is wrong.** `reconcile_journals` (`journal.py:192`) uses
  `problem_exists(domain, problem_id)` as its proxy for "did the transaction
  commit" — its docstring says exactly that, and `ArtifactPromoter`'s repeats
  it. An edited problem exists either way, so recovery always takes the "names a
  committed problem; clearing it" branch, deleting the journal without rolling
  anything back.
- **The API is package-shaped.** `ArtifactPromoter.stage()` takes a
  `ProblemPackage` and rebuilds the whole directory from `package.test_cases`. A
  Save has a delta.

## What to build

Add an edit-aware swap **alongside** the import path rather than bending the
import path to serve both. Import behavior must not change.

1. **Quarantine, don't delete.** Promotion moves an existing target to a hidden
   sibling instead of `remove_path` — `hidden_sibling()` already produces
   same-filesystem staging names. Rollback restores the quarantined original;
   `finish()` deletes it only after the commit succeeds. This is the
   displace-and-restore shape of `ContestFileQuarantine`, which
   `ArtifactPromotion`'s own docstring already cites as its model.
2. **Journal the quarantine.** Journal entries record the quarantined path
   beside `staged` / `target` / `root`, so startup reconciliation can *restore*,
   not merely delete. Keep the guarded, `fsync`'d write and the
   re-validate-every-path-against-its-root rule.
3. **Fence on the generation, not on existence.** The journal records the
   `artifact_generation` value (column added in Phase 2) that the Save expects
   the problem row to hold after commit. Recovery compares the stored value:
   - equal ⇒ the commit landed → keep the new artifacts, drop the quarantine;
   - lower ⇒ the commit did not land → restore the quarantined originals.
   `problem_exists` remains the predicate for **import** journals only;
   `reconcile.py` dispatches on journal kind.
4. **Every Save-path test-case mutation uses it.** No size or count exception. A
   Standard case is `.in` *and* `.out` plus database metadata, and
   `save_testcase_files` (`shared/services/testcase_files.py:57`) performs
   direct, non-atomic writes and may delete a stale `.out`. The retained
   satellite routes keep commit-then-write until they are deleted; the new Save
   path may not.

A Save materializes the complete desired test-case directory in staging, so a
Save touching one case still copies the problem's other cases into staging.
That cost is accepted: it buys a single same-filesystem rename at promotion and
a genuinely reversible swap.

## Save ordering this establishes

1. Parse and validate every field and operation.
2. Read uploaded content within existing size limits.
3. Build the complete proposed database and filesystem outcome without mutating
   durable state.
4. Stage new statement, image, validator, and test-case files in request-private
   locations.
5. Apply strategy-specific invariants to the final proposed state.
6. Promote staged content (quarantining what it displaces), then apply ORM
   mutations — including the `artifact_generation` increment — in a single
   `session.commit()`.
7. On commit failure, restore quarantined originals and remove promoted content
   through the registered rollback path.
8. Enqueue validator compilation only after a successful commit.
9. Redirect 303 to the appropriate tab.

Never commit database rows before unprotected filesystem writes.

## Files

`shared/services/problem_package/staging.py`, `promotion.py`, `journal.py`,
`reconcile.py`; `shared/services/testcase_files.py` (staged-swap write path).

## Tests

Exercise all four failure points, for both a Standard and an Interactive
problem that already has test data:

- **Before promotion** — staging fails: durable state untouched, staging area
  cleaned, no journal left behind.
- **Between promotion and commit** (simulated crash, no rollback runs) —
  startup reconciliation compares `artifact_generation`, finds it unchanged, and
  **restores the problem's previous files exactly**. This is the case the import
  machinery would have left empty.
- **During commit** — the commit raises: rollback restores the previous files
  exactly; the problem is byte-identical to its pre-Save state.
- **After commit** (crash before the journal is erased) — the generation matches,
  so recovery keeps the new artifacts and drops the quarantine. Nothing is
  rolled back.

Plus:

- Import journals still reconcile on problem existence and are unaffected by the
  generation fence.
- A quarantine path naming a target outside the configured roots deletes and
  restores nothing.
- An unreadable or unknown-version journal is left in place, as today.

## Gate

All four failure points restore or retain the correct bytes, import behavior is
unchanged, and the recovery tests run in CI. Phase 6 does not start until this
gate is green.
