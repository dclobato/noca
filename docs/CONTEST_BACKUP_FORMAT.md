# Full Contest Backup Format (v4)

This document describes the portable ZIP archive produced by the Web
`contest_backup_service` when an uberadmin exports a whole contest, and how the
companion importer restores it faithfully.

The goal is **backup and faithful historical replay**: a finished contest is
serialized and later re-imported under a new name/slug with replay-relevant
rows, verdicts, timings, and timestamps written **verbatim**. Nothing is
re-judged.

This is distinct from the per-problem package importer
(`web/services/problem_service/importing.py`), which is intentionally lossy
(new balloon color, all cases become secret, no timestamps preserved, active
validator downgraded to a pending candidate). The backup path never reuses that
importer; it serializes DB metadata itself via SQLAlchemy Core and reuses the
per-problem package *file* layout only for bulky payload bytes.

## Archive layout

The archive uses the following fixed top-level members and one directory for
each problem; the layout is compatible across all supported versions.

```
contest-backup-<slug>-<YYYYMMDD-HHMMSS>.zip
├── manifest.json          format_version, exported_at, includes flags
│                          (include_password_hashes, include_media), lossless
│                          contest row, sites[], language_ids[], and
│                          problems[] map {original_id, ordinal, dir}
├── problems.json          per-problem DB metadata: problem row, test_cases[],
│                          language_limits[], sample_interactions[],
│                          custom_validator (or null), categories[] (names), dir
├── users.json             all ALL_CONTEST_ROLES users, verbatim ids.
│                          password_hash present only when chosen at export.
├── media.json             users_media rows (only when include_media chosen)
├── submissions.json       every submission row (source + hash + timings)
├── judgments.json         FULL judgment history (incl. SUPERSEDED/FAILED); each
│                          entry nests test_results, confirmations, overrides,
│                          interactive_attempts, and submission_judgment_audit
├── clarifications.json    every clarification row (incl. soft-hide fields and
│                          the requesting team's `answer_read_at` marker);
│                          `problem_id` is null for general, contest-wide ones
├── tasks.json             every staff task row
└── problems/<ordinal:03d>/  per-problem package payload from build_export_zip:
                             statement.md|pdf, in/NNN.in, out/NNN.out,
                             optional editorial.md, and other package members
                             (editorial, image, validator, and interactions are
                             restored from the lossless JSON rows)
```

Only the **statement** and **test-case input/output files** are read back from
the per-problem folder on import; every other value (image base64, validator
source, editorial Markdown, and sample-interaction transcripts) is restored from
the JSON rows.

## Serialization contract

Rows are stored as plain JSON objects keyed by column name. Round-tripping
through JSON only needs two special cases:

- `DateTime` columns are ISO-8601 strings, parsed back with
  `datetime.fromisoformat`.
- Enum values are their string `.value`, accepted verbatim on Core insert.

Everything else (`str`, `int`, `bool`, `null`, JSON columns) is stored as-is.

## Restore fidelity and remapping

The whole restore runs in **one transaction** with Core `insert()` (bypassing
the submission-immutability and judgment-`final_verdict` ORM hooks so rows land
verbatim), committing exactly once at the end. All new rows get fresh UUIDs;
old→new id maps are built for users, sites, problems, test cases, submissions,
and judgments and applied to every foreign key. Every file is written under a
**new-UUID-namespaced path**, and any failure removes every file written so far
and rolls back.

Deliberate transformations on restore:

- **Contest:** inserted with the exported timing (including a past
  `start_time`), a new `id`, the chosen new name/slug, and
  `created_by_uberadmin_id` set to the importing uberadmin. `owner_user_id` /
  `chief_judge_id` are relinked to the remapped user ids after users exist.
- **Users:** `created_by_uberadmin_id` = importing uberadmin,
  `created_by_admin_id` = NULL (to satisfy
  `ck_users_exactly_one_creator`). When password hashes are excluded, each
  `password_hash` is set to a freshly generated **unusable random** hash (never
  NULL).
- **Custom validator:** restored as its active revision, **data only** — all
  candidate fields are cleared and no compile job is enqueued. A restored
  interactive problem is display-only until a validator is re-staged.
- **`problems.output_limit_in_bytes`:** the column used to be nullable, with NULL
  meaning "no limit" — which the judge already clamped to the global ceiling
  anyway. It is now NOT NULL, and row validation rejects NULL for a non-nullable
  column, so an older backup carrying `null` would otherwise be **unrestorable**.
  A missing or null value is therefore normalized to the documented default,
  65536, before validation. Nothing else about such a backup changes.

## Sensitivity gates

`password_hash` and `users_media` are optional, chosen with two checkboxes at
export time. Exporting password hashes requires the uberadmin to reconfirm their
password, writes a `security_events` / `admin_audit` row, and shows an
offline-cracking/reuse warning.

## Validation (before any write)

The importer applies these gates before it creates database rows or files.

1. **Manifest gate:** a supported `format_version` (1, 2, 3, or 4), presence of all
   required JSON members, and every referenced per-problem folder.
2. **Safe members:** reject path traversal, absolute names, drive letters,
   duplicate members, and excessive member counts.
3. **Bounded reads:** enforce compressed-upload, per-member, JSON-member, and
   total uncompressed size ceilings. Payload files are read one at a time.
4. **Schema and graph:** reject missing or unknown columns, duplicate IDs and
   ordinals, dangling references, mismatched contest scope, and missing
   statement or test-case files before restore starts.
5. **Slug:** validate format, length, and availability.
6. **Languages fail-closed:** require every referenced `language_id` to be
   registered on this server.

## Out of scope

The historical replay format intentionally excludes these operational assets.

- Queue re-judging (historical restore only)
- Uberadmin accounts
- Compiled validator binaries (only source is stored)
- Auto-Limit profiling runs and profiling case results
- Solution-test runs and their case results
- Problem-limit change batches and their operational re-judging state
- Per-team clarification read markers (`clarification_reads`)

The read markers are excluded deliberately rather than by omission. They are
per-team UI state, not contest content, and their absence means "unread" — the
safe default for a restored contest, where every announcement it carries should
show as new to the restored teams.

## Versioning

The archive is versioned by `format_version` (currently `5`, `FORMAT_VERSION` in
`web/services/contest_backup_service/models.py`). This server restores **version 5
only**; anything else is refused with a message naming the supported version. Bump it
on any breaking layout change and update this document.

### Version 5: the public export counter, and the end of legacy restore

Version 5 adds the `public_export_generation` column to every row in `problems.json`.
Row validation compares an archived row against the **live** table, so the column is
mandatory: a v5 archive that omits it is refused as malformed. Nothing infers it — the
counter is per-deployment cache state with no meaning across installs, and a restored
problem simply carries whatever the archive recorded, with its first export built on
demand.

The same bump **retires versions 1 to 4**, and that is the larger change. Each retired
version carried its own set of columns-it-predates plus an inference rule for what those
columns would have held: the validation strategy guessed from custom-validator presence,
the announcement flag guessed from the archived author's role. Every such rule had to be
written once and consulted from *both* the integrity checker and the restorer, because
the two disagreeing means an archive validates as one kind of row and restores as
another. Version 5 states every column, so the entire inference layer is gone — along
with `strategy.py`, `announcement.py`, the per-version optional-column tables, and the
"version 1 covers two archive shapes" ambiguity that made those predicates necessary in
the first place.

The cost is stated plainly: an archive captured by an earlier release cannot be restored
by this one. Restore it with the release that wrote it, or re-export the contest from a
server still running that release before upgrading.

### Retired versions

Versions 1 to 4 are no longer restorable. They are recorded here only so an operator
holding such an archive can tell what it is:

| Version | Introduced |
| --- | --- |
| 1 | The original layout. Two shapes existed in the wild: captured before `problems.validator_type` existed, and captured after it landed but before the format bump, still labelled version 1. |
| 2 | The stored validation strategy (`validator_type`) and `artifact_generation` as mandatory problem-row columns, plus embedded version 2 problem packages. |
| 3 | The nullable problem `editorial` column, required even when `null`. |
| 4 | The stored `clarifications.is_announcement` flag, required as a row key. |

### Why an archive states every column

Writing a deliberately reduced archive — omitting a column to stay compatible with an
older server — was considered and rejected. It is lossy in exactly the cases these
columns exist to fix: a standard problem carrying a stale validator row would restore as
interactive, and an interactive problem whose source was removed has nothing to infer
from and would restore as standard. Silent corruption on a round trip is worse than the
forward-compatibility cost, which is that an archive written by this version is rejected
by an older server with a clear error rather than restored wrongly — the same bargain,
in the same direction, that retiring the legacy branches makes.

A consequence worth stating: the strategy also decides whether a
`problems/NNN/out/NNN.out` payload member is required. Expected output is required by
the *strategy*, not by whether a validator row happens to hold active source, and with
the strategy always present that check reads one column instead of reproducing an
inference.

### The embedded packages are not the restore source of record

Each problem folder embeds a `full` problem package, but **restore never parses
its `problem.json`**: it reads the JSON payload rows and the `in/`/`out/` members
directly. The embedded package is a convenience artifact for operators who want
to extract one problem.

That distinction matters because problem-package version 2 cannot represent an
interactive problem with no validator source, and a full export normally refuses
one. Applying that rule here would make a contest **un-backupable** merely because
one problem's validator source was removed — a state the application supports by
design. The backup exporter therefore passes `require_importable=False`, and the
validator row itself is preserved verbatim in `problems.json`, so nothing is lost.
The consequence to know about is that such an embedded package, extracted and fed
to a problem importer on its own, is refused.
