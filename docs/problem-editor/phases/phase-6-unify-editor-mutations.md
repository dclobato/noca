# Phase 6 — Unify editor mutations

Route every mutation initiated inside the tabbed editor through the single
**Save problem** action, so no action on the page can discard unsaved work in
another tab.

Depends on **Phase 4** — the edit-aware staged swap must already exist and be
proven. Depends on **Phase 5** for the tabs the pending state lives in.

Authoritative detail: `docs/problem-editor/PLAN.md`, "Phase 5 — Fold every mutating action
into Save".

## Pending operations

New fields attached to `#edit-form`:

| Field | Replaces |
| --- | --- |
| `tc_bulk_zip` (file) | `upload_testcase_zip` / `arena_admin_problem_tc_zip_replace` |
| `tc_add_zip` (file, repeatable) | `add_test_case_zip` / `arena_admin_problem_tc_add_from_zip` |
| `tc_replace_zip_{tc_id}` (file) | `replace_test_case` / `arena_admin_problem_tc_replace` |
| `tc_sample_toggle_ids` (hidden CSV) | `toggle_test_case_sample` / `arena_admin_problem_tc_toggle_sample` |
| `validator_language_id`, `validator_source_file` | the nested validator upload form |

Per-row file inputs live in a hidden container **outside** the swapped table
fragment, keyed by test-case id, so selected `File` objects survive a reorder
swap.

**Validator removal folds in too:** the removal modal's confirm submits the main
form with a validated action field, carrying the existing keep-or-destroy choice
for sample interactions. The Save runs first, the confirmed removal applies, the
redirect returns to `?tab=test-cases`. Removal never changes `validator_type`.

## Client state

Grow `shared/static/js/tc-pending-remove.js` into **`tc-pending-state.js`**,
owning removals, pending replaces, and pending sample toggles. It:

- renders each row's optimistic state with an undo;
- dispatches the existing `noca:problem-edit-changed` event;
- re-applies visible pending state from the hidden inputs when the new
  `noca:tc-list-swapped` event fires after a reorder swap;
- **disables reorder as soon as any mutually exclusive pending operation
  exists** — in particular the moment a bulk ZIP is selected — and says why.

`tc-replace-row.js` stops auto-submitting its own form and instead marks the row
pending.

## Conflict handling

Reject conflicting combinations before any mutation, with a clear error. Never
silently pick a precedence.

Server-side, in the Save route:

- A bulk replace-all ZIP is mutually exclusive with inline additions, added
  single-case ZIPs, per-row removals, per-row replacements, and sample toggles.
- Validator removal is mutually exclusive with uploading a replacement validator.

Client-side, because reorder stays an immediate POST and therefore has no
pending representation the route could reject: reorder is disabled while a
conflicting pending operation exists. **State rehydration is usability, not
enforcement** — do not rely on it for the conflict rule.

## Save ordering

Use the ordering Phase 4 established, including the `artifact_generation`
increment inside the commit and the quarantine-restoring rollback. Every
test-case mutation on this path uses the edit-aware staged swap — no size or
count exception.

ZIP semantics stay strategy-aware: Standard requires an output file per case;
Interactive accepts input-only archives
(`shared/tc_zip.py:parse_single_testcase_zip(..., require_output=False)`) and
never silently picks up an output member.

## The file-input failure mode

Browsers cannot repopulate `<input type="file">` after a failed validation, so a
Save that fails validation discards every staged archive at once. Accepted cost
of the single-transaction model, mitigated:

- On a validation-failure re-render, show a prominent alert naming each staged
  upload that must be re-selected, driven off the field names present in the
  rejected POST.
- **Re-emit the typed inline test-case rows.** This is not already true, despite
  an earlier draft of the plan saying so: no render path echoes `tc_in_N` /
  `tc_out_N` / `tc_explanation_N` / `tc_is_sample_N` back, so today a failed Save
  discards every row the author typed. Confirmed by usage testing on 2026-08-13,
  where a malformed sample-interaction transcript took the pending test cases
  with it. Four render paths need it — Web create and edit, Arena create and
  edit — plus a partial that renders a pre-filled row, which is the same markup
  `shared/static/js/tc-add-row.js` builds client-side. Cover it with a test per
  module: submit rows alongside a failing field, assert the rows come back.
- Validate cheap, common metadata failures client-side (required title, numeric
  limits) before submit, so the usual near-miss never reaches the server with
  files attached.
- Keep pending-state markers rendered after the failed Save, so the author sees
  which rows still expect a file.
- A ZIP validation failure leaves the existing test-case set unchanged; prefer
  atomic whole-request failure over a partially applied Save.

## Satellite routes

The immediate bulk, add, replace, and toggle routes stop being referenced by the
editor UI but are **retained** in this release: apply the explicit strategy
checks to them, make them redirect to `?tab=test-cases`, and mark them
deprecated in both `ROUTES.md` files. They are deleted in a **follow-up
release**, once the pending model has proven out — deleting them here enlarges
the release and removes the fallback if the new path has a bug. Their existing
tests keep them honest meanwhile.

**Reorder** (`move_test_case_route`, `arena_admin_problem_tc_move`,
`interaction_move`) is the sole immediate action that remains part of the
design: idempotent, already a fragment swap.

## Tests

- Save applies pending add, remove, replace, and toggle operations; undo
  restores the original pending state.
- A bulk-ZIP Save preserves unsaved metadata and statement edits and returns on
  the Test cases tab.
- Bulk-replace conflicts and remove-plus-replace validator conflicts are
  rejected before any mutation.
- Reorder is disabled once a bulk ZIP is selected.
- A failed Save re-emits the typed inline test-case rows, in both modules and in
  both create and edit mode.
- Pending markers **and selected files** survive a reorder swap.
- A ZIP that fails to parse leaves the existing test-case set and the saved form
  intact.
- Validator upload stages a candidate and enqueues validation only after commit;
  replacement preserves the old active revision until promotion.
- Validator removal through the modal saves the form first and returns to the
  Test cases tab.
- Standard inline and ZIP cases require output; Interactive accepts input only
  and forces secret.
- Standard problems reject validator actions through the direct routes.
- A validation failure re-render names every file input that must be re-selected.

## Gate

No action inside the editor discards unsaved work except the documented
file-input case, every conflict is refused before mutation, and the retained
satellite routes still pass their existing tests.
