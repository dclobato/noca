# Full Contest Backup Format (v1)

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

The version 1 archive uses the following fixed top-level members and one
directory for each problem.

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
├── clarifications.json    every clarification row (incl. soft-hide fields);
│                          `problem_id` is null for general, contest-wide ones
├── tasks.json             every staff task row
└── problems/<ordinal:03d>/  per-problem package payload from build_export_zip:
                             statement.md|pdf, in/NNN.in, out/NNN.out,
                             (image.* / validator/ / interaction/ are ignored on
                             import — those live losslessly in the JSON rows)
```

Only the **statement** and **test-case input/output files** are read back from
the per-problem folder on import; every other value (image base64, validator
source, sample-interaction transcripts) is restored from the JSON rows.

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

## Sensitivity gates

`password_hash` and `users_media` are optional, chosen with two checkboxes at
export time. Exporting password hashes requires the uberadmin to reconfirm their
password, writes a `security_events` / `admin_audit` row, and shows an
offline-cracking/reuse warning.

## Validation (before any write)

The importer applies these gates before it creates database rows or files.

1. **Manifest gate:** required `format_version`, presence of all required JSON
   members, and every referenced per-problem folder.
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

## Versioning

The archive is versioned by `format_version` (currently `1`, `FORMAT_VERSION` in
`web/services/contest_backup_service/models.py`). A server restores only its own
version. Bump it on any breaking layout change and update this document.
