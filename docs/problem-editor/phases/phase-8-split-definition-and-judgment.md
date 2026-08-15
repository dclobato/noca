# Phase 8 — Split the editor: definition vs judgment data

**Supersedes phase 7.** Phase 7 was "documentation and validation" for the unified
editor phase 6 produced. Usage of that editor changed the design instead, so this
phase carries both the change and phase 7's document list.

## Why

Phase 6 achieved its goal: no action discarded unsaved work, because every
editor-initiated mutation rode one Save. It paid for that with a large
client-side pending model — optimistic markers, undo, rehydration after a
reorder swap, conflict rules between combinations, and a re-emit path for
rejected saves — and with a Save route that carried every concern at once.

The cost sat in the wrong place. Metadata and the statement are authored once;
test cases are iterated on, are numerous, and can be large. Forcing both through
one form meant the page held state the server had never seen, and the backend had
to reconcile intentions that could contradict each other.

## What shipped

Two editors, reached from the problem list.

- **Definition editor** (the pencil action) — metadata, statement, and Contest's
  limits. One form, one Save, client-side panes. Identical for every strategy.
- **Judgment-data editor** (a new `rule` action, "Judgment data") — test cases,
  custom validator, sample interactions. Its tabs are separate **pages**, because
  a problem can carry many large cases.

On the judgment pages, every operation on existing data posts immediately. Only
rows typed inline wait for that page's own Save; an upload that would discard them
says so first, and the unsaved guard covers navigation away. The client-side
pending model and every conflict rule are deleted.

Creation collects the definition only and redirects to the judgment pages — the
validator page for an interactive problem, which cannot be judged until a
validator compiles, and the test-cases page otherwise.

## What did not change

The durability contract. An action that changes rows **and** files still stages
the complete desired test-case directory, promotes it, commits, and finishes,
under the problem row's lock and behind the `artifact_generation` fence.
`shared/services/judgment_case_action.py` owns that ordering so it is written once
rather than once per endpoint. Actions that change no file — the sample toggle,
validator upload and removal, every interaction mutation — commit directly under
the lock, which is a stated rule rather than an oversight.

## The load-bearing change

Per-action posts made staging's byte-copy seed untenable: toggling one case on a
5 GB problem would have copied 5 GB, precisely on the problems that motivated
separate pages. Staging now seeds with `os.link`, and the invariant that makes it
safe is that nothing is ever written *through* a link — writes go to a temporary
name in the same directory and are renamed into position, so a staged file never
shares an inode being modified. Where the filesystem has no hardlinks the seed
copies and logs one WARNING naming the directory.

That made an already-existing, undocumented requirement explicit: the test-case
root must support atomic same-directory renames. `README.md`, `docs/BOOTSTRAP.md`
and `docs/CONFIG.md` now say so, along with which filesystems (FAT32/exFAT, some
SMB and FUSE mounts, Docker Desktop bind mounts from Windows or macOS hosts) fall
back to copying. Windows itself is not the constraint: NTFS supports hardlinks.

## Commit sequence

Each commit left the tree shippable.

1. Durability conversion in place — every per-action test-case route converted to
   the swap at its current path, `PendingTestCaseOps.order` for reorder,
   `judgment_case_action.py`, hardlink seeding, the missing lock and strategy
   gates. Nothing depended on it yet; everything later did.
2. Per-action URLs on `TestCaseRowView`, behaviour unchanged.
3. Contest judgment routes, pages, and list action — both editors coexisting.
4. Arena judgment routes, pages, and list action.
5. Both editors narrowed to the definition; the view model split into
   `ProblemDefinitionView` and the judgment page views.
6. Both create routes narrowed; post-create redirect repointed.
7. The client-side pending model and the duplicated satellite endpoints deleted.
8. Documentation (this file, and phase 7's list).

## Documents this phase owns

`web/docs/ROUTES.md` + `URL_FOR_REFERENCE.md`, the Arena pair,
`docs/SHARED_SERVICES.md`, `docs/ARCHITECTURE.md`, `docs/PADROES_UI.md`,
`README.md`, `docs/BOOTSTRAP.md`, `docs/CONFIG.md`, and
`docs/custom-validator/INTERACTIVE_VALIDATOR.md`.
