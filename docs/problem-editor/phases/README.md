# Validator-specific tabbed problem editor — phased execution

One PR / one release, delivered as gated commits. The authoritative design is
[`../PLAN.md`](../PLAN.md); these files are the execution slices, and where they
disagree the combined plan wins.

Phase 8 supersedes phase 7: the unified editor phase 6 produced was split into a
definition editor and a judgment-data editor before phase 7's documentation pass
ran, so phase 8 carries both the change and that document list.

| Phase | File | Gate |
| --- | --- | --- |
| 1 | [phase-1-correct-the-plan.md](phase-1-correct-the-plan.md) | Plan corrections verified against the codebase |
| 2 | [phase-2-persist-strategy.md](phase-2-persist-strategy.md) | Strategy stored and authoritative; UI unchanged and still working |
| 3 | [phase-3-version-packages-and-backups.md](phase-3-version-packages-and-backups.md) | v2 packages and v2 backups, both v1-compatible |
| 4 | [phase-4-safe-editor-transactions.md](phase-4-safe-editor-transactions.md) | Edit-aware artifact swap proven under four failure points |
| 5 | [phase-5-chooser-and-tabbed-editor.md](phase-5-chooser-and-tabbed-editor.md) | Chooser + tabs shipped; satellite routes untouched |
| 6 | [phase-6-unify-editor-mutations.md](phase-6-unify-editor-mutations.md) | Every editor-initiated mutation goes through Save |
| 8 | [phase-8-split-definition-and-judgment.md](phase-8-split-definition-and-judgment.md) | Two editors shipped; docs complete, full suite green |

## Ordering rules that matter

- **Phase 4 precedes Phase 6.** The edit-aware swap must exist and be proven
  before any ZIP upload is connected to the main Save. Folding archives into a
  Save that still commits before writing files is the one sequencing mistake
  that loses author data.
- **Phase 2 precedes Phase 3.** Package and backup versioning read the stored
  strategy; they cannot be written against the inferred one.
- **Phase 5 precedes Phase 6.** Build the tabs while the satellite routes still
  work, so the editor is never in a half-migrated state.
- Phases 2–6 each leave `master` shippable. Satellite routes stayed functional
  until Phase 6 retired them from the UI; Phase 8 deleted the ones its judgment
  routes duplicate and kept the per-case ones.

## Standing requirements for every phase

- Run `uv run ruff format . && uv run ruff check --fix .` and
  `uv run mypy web shared autojudge arena rating aiassistant healthmonitor animator`
  before committing.
- Reformat touched templates with `uv run djlint <paths> --reformat`.
- Update the copyright header on every modified source file to the current
  required form; do not touch otherwise-unchanged files just to update headers.
- Keep new source files inside the 100–300-line target.
- Update `ROUTES.md` / `URL_FOR_REFERENCE.md` / `SERVICES.md` in the same commit
  as the route or service change that requires it — not deferred to the final
  phase, which owns the *narrative* documents rather than the per-change
  reference updates.
