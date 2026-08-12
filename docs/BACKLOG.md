# Backlog

This document tracks accepted NOCA contracts that still require implementation
work. An item remains here until the implementation, tests, and related
documentation match the settled behavior.

## Autojudge

Autojudge backlog items cover judging runtime behavior shared by Web and Arena.

### Implement output-checker persistence and diagnostic capture

**Status:** Pending.

Output-checker judgments must preserve normal per-test-case contestant results
and separate checker-attempt diagnostics. The settled retention, byte-capping,
decoding, visibility, and snapshot contracts are documented in
[Output checker](custom-validator/OUTPUT_CHECKER_VALIDATOR.md#persistence-and-diagnostics).

Complete the schema and persistence portion of this backlog item by making
these changes:

- Add equivalent `submission_output_checker_attempts` and
  `arena_submission_output_checker_attempts` tables, with cascading judgment
  foreign keys and unique `(judgment_id, attempt_number)` constraints.
- Store the test-case ordinal, attempt number, checker exit code and signal,
  checker wall time, memory, stdout byte count, bounded stdout and stderr,
  `stdout_truncated`, clean checker verdict or typed internal-failure reason,
  and creation time.
- Add constraints that permit only attempts 1 and 2 and prevent a clean checker
  verdict from coexisting with an internal-failure reason.
- Analyze the insert-delete churn from last-case-only retention and add
  per-table autovacuum tuning in the migration when the expected write pattern
  warrants it.
- Add equivalent non-scoring attempt storage for solution tests, profiling, and
  Auto-Limit without creating scoring submission judgments.
- Include Web Contest checker-attempt records in contest backup, integrity
  validation, restore, and contest-removal workflows.

Complete the Autojudge portion by making these changes:

- Split contestant execution persistence from checker-attempt persistence so a
  retry never duplicates or replaces the normal per-test-case result.
- Clear all prior checker attempts when attempt 1 starts a new case, and replace
  only an existing attempt-2 row when persisting attempt 2.
- Persist a typed staging-failure attempt with null process fields when the
  checker cannot start after input preparation begins.
- Retain the first 256 KiB of checker stdout with a `stdout_truncated` flag and
  the first 16 KiB of checker stderr without a flag or stored marker.
- Continue draining both streams after their persistence caps so diagnostic
  capture cannot block execution or affect the verdict.
- Decode retained bytes as UTF-8 with replacement and remove NUL characters by
  reusing the existing database-text decoding helper.
- Remove the complete contestant output and all staged checker files after the
  checker workspace is destroyed; persist only the standard bounded contestant
  excerpts.
- Load source, language, semantics, test data, and reference data from the
  active configuration without adding historical snapshot or revision foreign
  keys in the initial implementation.
- Add focused persistence and runner tests for last-case replacement, both-row
  retry retention, recovered duplicate writes, byte-boundary truncation,
  invalid UTF-8, NUL removal, staging failures, and cleanup of ephemeral files.

### Stage coherent checker reference data

**Status:** Pending.

Output-checker jobs must stage optional opaque UTF-8 reference text under the
[settled reference-output contract](custom-validator/OUTPUT_CHECKER_VALIDATOR.md#reference-output-contract).

Complete this backlog item by making these changes:

- Load one coherent, ordered copy of all test-case inputs and optional reference
  outputs when a judging, solution-test, profiling, or Auto-Limit job is
  dispatched.
- Preserve the canonical LF-normalized reference bytes and the
  absent-versus-empty distinction when staging `/data/reference.out` for each
  checker attempt.
- Keep an already-running job on its job-local copy when an administrator edits
  test-case data, while an undispatched queued job uses the latest published
  case set.
- Treat an intentionally absent reference file as valid staging. Let a checker
  that requires it report exit code `3` as a non-retried contract failure.
- Destroy the job-local test-case copy with the job and do not add historical
  test-case revision persistence in the initial implementation.
- Add concurrency tests proving that a mid-judgment edit cannot mix old and new
  files within one judgment, plus tests for absent, empty, Unicode, and mixed
  reference-output cases.

### Align interactive solution-test diagnostics

**Status:** Pending.

Interactive solution-test attempts already share the last-case-only retention
model used by submission judgments, but their result rows do not retain the
complete validator-side diagnostic contract documented in
[Interactive validator](custom-validator/INTERACTIVE_VALIDATOR.md#solution-test-attempts).

Complete this backlog item by making these changes:

- Extend `solution_test_case_results` interactive rows with validator exit code,
  validator signal, validator stderr excerpt, clean validator verdict, enforced
  limit outcome, and typed crash reason fields.
- Preserve the existing contestant exit, signal, stderr, measurements,
  transcript, ordinal, and attempt number without duplicating ordinary
  solution-test rows.
- Keep at most attempts 1 and 2 for the last executed test case, including both
  rows after a crash and successful retry when that case remains last.
- Render both process outcomes and both stderr excerpts to administrators and
  judges authorized to view the solution-test run.
- Add schema, persistence, retry, decoding, truncation, authorization, and UI
  tests for the aligned attempt record.

### Remove `PER_LANGUAGE_LIMITS` from custom validators

**Status:** Pending.

Interactive validators and output checkers must receive only the submitted
language ID and that language's effective problem limits:

- `PROBLEM_TIME_LIMIT`;
- `PROBLEM_OUTPUT_LIMIT`;
- `PROBLEM_MEMORY_LIMIT`;
- `PROBLEM_PID_LIMIT`;
- `USER_LANGUAGE`.

The current Autojudge still constructs and injects `PER_LANGUAGE_LIMITS` for Web
interactive-validator submissions and solution tests. Remove this environment
variable because a validator processes one submitted language and must not
depend on limits configured for unrelated contest languages. The settled
contracts are documented in
[Interactive validator](custom-validator/INTERACTIVE_VALIDATOR.md#limit-metadata-available-to-the-validator)
and [Output checker](custom-validator/OUTPUT_CHECKER_VALIDATOR.md#checker-environment).

Complete this backlog item by making these changes:

- Stop loading every contest language's effective limits for validator jobs.
- Remove `per_language_limits` from validator preparation and execution APIs.
- Stop serializing and injecting `PER_LANGUAGE_LIMITS` into validator
  containers.
- Remove the database accessor for all-language effective limits if it has no
  remaining consumers.
- Update focused Autojudge tests to assert that only the five contract variables
  are injected.
- Verify that each core limit still uses the submitted language's override or
  the problem-level fallback and that the output limit still respects NOCA's
  global ceiling.

## Shared problem data and packages

Shared backlog items cover test-case storage and package behavior used by both
Web and Arena.

### Preserve optional checker reference text without interpreting it

**Status:** Pending.

The initial checker format supports one optional `.out` for every `.in`, with
independent presence per case and no arbitrary auxiliary files. Complete this
backlog item by making these changes:

- Make ZIP and package pairing rules strategy-aware: require `.out` for every
  `standard` case, forbid it for `interactive`, and permit it independently for
  each `checker` case.
- Reject an `.out` without its corresponding `.in`, while accepting checker
  archives that mix cases with and without `.out`.
- Validate every present checker `.out` as strict UTF-8 and reject binary or
  malformed input before publishing the test-case change.
- Normalize CRLF and lone CR line endings to LF when checker `.out` text enters
  NOCA through authoring, archive import, package import, or restore. Apply no
  other whitespace, Unicode, or final-newline transformation.
- Store and transport the normalized UTF-8 bytes as the canonical reference
  output. Calculate output-size metadata, package digests, and backup digests
  after normalization.
- Preserve a present empty file as zero bytes and an absent file as absent. Keep
  nullable output-size metadata consistent, with `0` for empty and `null` for
  absent.
- Limit the first package version to the existing single `.out` member per case
  and reject arbitrary checker auxiliary files.
- Include every present checker `.out` in full packages, test-case archives,
  backups, and restores. Include it in public packages only when its case is a
  sample.
- Extend package digests, round-trip tests, backup integrity checks, and restore
  tests to cover Unicode text, invalid UTF-8 rejection, CRLF and lone CR
  normalization, unchanged LF text, empty files, absent files, and mixed
  presence.

## Web and Arena

Web and Arena backlog items cover authoring and administration behavior that
must remain consistent across both problem domains.

### Manage and publish checker reference files

**Status:** Pending.

Problem-management and public problem pages must preserve opaque UTF-8
reference text without treating it as required expected output. Complete this
backlog item by making these changes:

- Let authorized problem owners upload, replace, delete, and download the one
  optional `.out` associated with each checker test case.
- Reject binary and invalid UTF-8 uploads, and normalize accepted line endings
  to LF. Use file operations as the source of truth, show a bounded read-only
  preview for every present file, and keep the complete canonical text
  available through download.
- On public sample views, place `.in` and present `.out` download actions
  together, serve `.out` as a `text/plain; charset=utf-8` attachment, show its
  bounded text preview, and omit both when the file is absent.
- Never expose secret checker inputs or reference files on contestant-facing
  pages or through public-package exports.
- Apply the shared test-case change and explicit full-rejudgment contract below
  whenever an input or reference output changes.
- Add Web and Arena authorization, upload, download, preview, public-sample,
  invalid-UTF-8, Unicode, and manual-rejudgment tests.

### Rejudge after custom-validator test-case changes

**Status:** Pending.

Interactive and output-checker test-case changes leave completed judgments
unchanged and require an explicit full rejudgment. Complete this backlog item by
making these changes:

- Treat adding, removing, reordering, or replacing interactive inputs and
  checker inputs or reference outputs as test-case changes.
- Do not automatically invalidate the active validator, disable the problem, or
  requeue submissions after a test-case change.
- Provide an explicit full-problem rejudgment action to authorized
  administrators and problem owners in Web and Arena.
- Requeue all eligible historical submissions only after that action is
  confirmed, and judge them with the active validator and test-case data loaded
  at their new dispatch time.
- Keep undispatched jobs on the latest published case set and running jobs on
  their coherent, ephemeral job-local copy.
- Add permission, confirmation, queueing, concurrent-edit, Web, Arena,
  interactive, and output-checker tests.

### Present output-checker diagnostics to authorized audiences

**Status:** Pending.

Web and Arena must render the persisted output-checker attempt records without
changing their failure ownership. Complete this backlog item by making these
changes:

- Show stdout as **Validation result** and stderr as **Validation errors** to
  the submission owner for clean checker exits `0`, `1`, and `2`, including a
  final `AC` attempt.
- Show the stdout truncation notice from `stdout_truncated`; do not infer or
  claim whether a 16 KiB stderr excerpt was truncated.
- Restrict detailed internal-failure diagnostics to authorized admins, judges,
  problem owners with management permission, and operators. Show only a generic
  internal judging failure to the submitting contestant.
- Let authorized Arena teachers see clean checker feedback for submissions they
  may inspect, but do not expose internal checker details unless they also have
  problem-management authority.
- Never expose checker diagnostics to other contestants or checker source to a
  submission owner through the diagnostic view.
- Label every administrative checker-source download as the current active
  source and do not imply that it is the source used for a historical judgment.
- Add Web and Arena authorization and rendering tests for submission owners,
  staff roles, authorized teachers, problem owners with management permission,
  and unrelated users.

### Make validation strategy immutable after problem creation

**Status:** Pending.

The author must select `standard`, `interactive`, or `checker` when creating a
problem. After creation, the validation strategy is immutable. Changing to
another strategy requires creating a new problem from scratch. The settled
contract is documented in
[Output checker](custom-validator/OUTPUT_CHECKER_VALIDATOR.md#package-representation).

Complete this backlog item by making these changes:

- Add validation-strategy selection to the Web and Arena problem-creation
  workflows.
- Display the selected strategy as read-only in every problem-edit workflow.
- Reject strategy changes in routes, APIs, shared services, and package update
  operations rather than relying only on disabled UI controls.
- Continue to support staged source, language, and semantics replacement within
  the existing `interactive` or `checker` strategy.
- Keep the problem's strategy unchanged when its validator source is removed,
  and block submissions until another validator candidate becomes active.
- Reject package data whose `validator_type` differs from the existing problem's
  strategy.
- Require future interactive packages to declare `validator_type: interactive`
  with `custom_validator`, input-only secret cases, and optional sample
  interactions. Preserve the current mapping for older package versions.
- Add focused Web, Arena, service, and package tests for permitted replacement
  and rejected cross-strategy changes.
