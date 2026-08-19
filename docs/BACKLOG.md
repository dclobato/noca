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

### Make validation strategy immutable after problem creation (remaining gaps)

**Status:** Pending.

The core immutability guarantee for this contract is already implemented (see
[Implemented](#implemented) below). Two bullets from the original contract
remain open:

- The `checker` strategy is exposed as a choice in the Web and Arena
  problem-creation UI but kept on a disabled card, because checker validation
  itself is not implemented yet (see "Preserve optional checker reference text
  without interpreting it" above). Enable it once checker support lands.
- Reject package data whose `validator_type` differs from the existing
  problem's strategy. There is currently no package-based "update an existing
  problem" import path at all — `import_problem_package` in both Web
  (`web/services/problem_service/importing.py`) and Arena
  (`arena/services/admin_problem_io_service.py`) only construct brand-new
  problem rows — so this check has no code path to apply to yet. Implement it
  alongside any future package-based update flow.

## Web

Web backlog items cover behavior specific to the Web module.

### Harden the public problem-set archive cache

**Status:** Pending.

`web/services/problem_set_cache.py` caches the anonymous `GET /problem-set/{slug}.zip`
archive per contest so a burst of downloads costs one build instead of rebuilding on
every hit (see `docs/ARCHITECTURE.md` and `web/docs/SERVICES.md`). Three follow-ups
identified during code review remain open:

- `_cache_hit` re-hashes the complete cached archive with SHA-256 on every request,
  not just after a build. For a large contest archive this reintroduces an
  O(archive size) disk-read and CPU cost on every anonymous hit, undercutting the
  cache's purpose. Replace the full re-hash on each request with a cheap staleness
  check (e.g. comparing `stat()` mtime/size against the values recorded at publish
  time), falling back to a full digest recomputation only when that check is
  inconclusive.
- The per-slug `anyio.Lock` in `_build_locks` only serializes concurrent builds
  within one process (noted in the module docstring). A multi-replica Web
  deployment can still run one full build per replica simultaneously for a newly
  released contest's first request. Replace it with the existing Valkey-backed
  `shared/services/lock_service` for cross-process serialization, or explicitly
  keep the current in-process-only behavior if the duplicate first-build cost is
  judged acceptable.
- `_build_locks: dict[str, anyio.Lock]` grows by one entry per distinct contest
  slug ever requested, for the life of the process, with no eviction. Bounded in
  practice by the number of distinct contests, but worth pruning (e.g. dropping an
  entry once its build completes and is uncontended) for consistency with the rest
  of the codebase's care about unbounded process-lifetime state (health-monitor
  presence pruning, security-events reaper).

Note: `NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH` caches on local disk, which is
per-replica and does not survive a redeploy. Replacing it with an S3-compatible
object-storage endpoint would give a persistent, replica-shared cache and could
also subsume the cross-process locking gap above (e.g. via a conditional
"put-if-absent" write) instead of adopting `shared/services/lock_service`.

### Decouple problem-set/editorial release from scoreboard release

**Status:** Pending — design confirmed, not yet implemented.

`GET /problem-set/{slug}.zip` currently reuses `Contest.release_scoreboard_after_end`
as its sole release gate (see "Harden the public problem-set archive cache" above
and `docs/ARCHITECTURE.md`), so making a contest's scoreboard public also makes
its full problem package — including every secret test case, validator source,
and editorial — publicly downloadable, with no separate opt-out. This conflates
two distinct admin decisions.

Add a separate release flag (e.g. `Contest.release_problem_set_after_end`,
independent of `release_scoreboard_after_end`) so an admin can publish the
scoreboard without releasing the problem package and editorials, and gate
`problem_set_download` (`web/routes/problem_set.py`) and the "Problem set" button
on `contests.html` on the new flag instead of the scoreboard one. Add a migration,
an admin control alongside the existing scoreboard-release toggle
(`web/routes/contest_admin.py`), and tests covering every combination of the two
flags.

## Document rendering

### Render HTML/Markdown to PDF on demand

**Status:** Idea — not yet an accepted contract.

NOCA has no server-side HTML/Markdown-to-PDF rendering today: problem
statements are either an author-uploaded `statement.pdf` (validated, never
rendered) or `statement.md` rendered client-side in the browser
(`shared/static/js/noca-markdown.js`). A downloadable PDF of a
Markdown statement, and the still-unimplemented contest logistics document
conversion noted in `web/docs/SERVICES.md`, both need this capability.

DMOJ's `pdfoid` (Selenium + headless Chrome + CDP `Page.printToPDF`) was
evaluated and rejected as a direct dependency: it is a small, unmaintained,
unauthenticated, AGPL-3.0 service with no request limits or sandboxing around
the HTML it renders. The root `pyproject.toml` already depends on Playwright
(currently only for browser tests), which exposes the same `page.pdf()`
capability natively, is actively maintained, and is Apache-2.0 licensed.

A future implementation should be a small dedicated service or worker built on
Playwright rather than pdfoid, following the existing isolated-worker pattern
(`autojudge`/`rating`/`aiassistant`): Valkey-queued jobs, no direct Python
import into `web`/`arena`, and explicit limits on renderable input size and
network access to prevent SSRF/DoS from untrusted statement content.

### Server-side LaTeX/math rendering

**Status:** Idea — not yet an accepted contract.

Math in problem statements is rendered entirely client-side today: vendored
KaTeX `auto-render` runs after `marked.parse()` + `DOMPurify.sanitize()`
in the single shared pipeline (`shared/static/js/noca-markdown.js`), for every
Markdown surface including test-case explanations.
`shared/problem_statement_markdown.py` never
inspects LaTeX delimiters — `$...$` / `\(...\)` pass through the sanitizer
untouched and are only ever interpreted by the browser.

Wikimedia's `mathoid` (Node.js/Express, wraps `mathoid-mathjax-node` plus
native `librsvg` bindings) was evaluated and rejected for the same reason as
`pdfoid` above: its last npm release was 2022-07-01, and adopting it means
introducing a Node.js runtime that does not otherwise exist anywhere in NOCA
(the `landingpage` module explicitly avoids one). It also does not solve a
problem NOCA currently has — client-side KaTeX works, and the moment a
Playwright-based PDF export (see above) exists, it renders the same
client-side KaTeX correctly for free, since Playwright drives a real browser.

Server-side math rendering is only worth revisiting for a narrower,
not-yet-stated need such as MathML/speech output for accessibility or
dropping the KaTeX bundle from clients. If that need materializes, prefer
reusing the same Playwright sidecar proposed for PDF export (headless-browser
KaTeX-to-SVG/MathML pre-rendering) over adding a second, stale, non-Python
service, and cap input size/timeout regardless, since pathological TeX macros
are a known DoS vector for MathJax-based renderers.

### Add a `table-caption` Markdown directive

**Status:** Idea — not yet an accepted contract.

NOCA's shared Markdown pipeline supports three one-line directives —
`::: table-border`, `::: table-align`, and `::: align` — all parsed and applied
by `shared/static/js/markdown-directives.js`. A fourth directive,
`::: table-caption <text>`, would render its text as a caption below the
following table, in italics, at a smaller font size, and constrained to 75% of
the available width.

The change is small, but it is the first directive that *emits content* rather
than toggling a presentation class on the block that follows it, so it needs
three decisions the existing directives never had to make:

- Every current directive takes a closed enum value (`DIRECTIVE_PATTERN` ends
  `([a-z]+)\s*$`), which is what makes `parseDirective` a pure whitelist. A
  caption takes free text, so the pattern must gain a separate alternative
  rather than loosening the existing one — otherwise a typo in `align` stops
  falling through to visible text.
- Directives run last in the pipeline (step 7, after `DOMPurify.sanitize()` and
  KaTeX). The marker paragraph's children are therefore already parsed,
  sanitized, and math-rendered. The implementation must strip the
  `::: table-caption ` prefix from the marker's first text node and reuse the
  remaining children, never re-render `textContent` as `innerHTML`, which would
  reintroduce unsanitized markup after the sanitizer has run.
- The caption attaches *below* its table, unlike every existing directive, which
  targets `nextElementSibling`. Decide whether the caption tracks the table's
  `::: table-align` value or always centers. A sibling element cannot measure
  the shrink-to-fit table it describes; a real `<caption>` child with
  `caption-side: bottom` can, and `common.css` already excludes captions from
  cell borders through `table > :not(caption)`.

Implementing it would touch the directive parser and applier
(`shared/static/js/markdown-directives.js`), one rule block under the shared
`:where(.noca-markdown, .editor-preview)` selector in
`shared/static/css/common.css`, the author-facing directive reference in
`shared/template/_partials/markdown_syntax_modal.html`, and optionally the
editor's table-starter action in
`shared/static/js/problem-statement-editor-core.js`. No server-side, schema, or
package changes are involved.

Note that `tests/shared/test_markdown_directives.py` runs the script in a Node
`vm` with stubbed globals and no DOM, so it covers `parseDirective` and
`prepareMarkdown` but not `apply`. The parser half of this directive is
unit-testable within the existing harness; the DOM half is not, without adding a
DOM implementation as a development dependency.

## Implemented

Items below were accepted contracts tracked in this document and have since
landed. They are kept here, rather than deleted, as a pointer to the commit
that implemented each one.

### Make validation strategy immutable after problem creation (core guarantee)

**Status:** Implemented.

The core immutability guarantee — selecting `standard`, `interactive`, or
`checker` at problem creation, exposing it as read-only afterward, and
enforcing that at both the ORM and route/service level — landed in
`f5575dd3` (`feat(problems): [phase 2] store the validation strategy
explicitly`) and `5f76f393` (`feat(problems): [phase 5] choose the validation
strategy before creating`):

- `shared/services/validator_type_guard.py` enforces immutability through a
  `before_flush` ORM guard, wired into both `web/models/problem.py` and
  `arena/models/arena_problems.py`.
- Web (`web/routes/contest_admin_problem_new.py`) and Arena
  (`arena/routes/admin_problem_new.py`) problem-creation routes present a
  validation-strategy chooser (`shared/services/validator_choice.py`); edit
  forms show the strategy as fixed and read-only thereafter.
- Focused tests live in `tests/shared/test_validator_type_immutability.py`.

Two narrower bullets from the original contract remain open — tracked under
"Make validation strategy immutable after problem creation (remaining gaps)"
above.
