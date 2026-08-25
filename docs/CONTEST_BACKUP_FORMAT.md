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

## Versioning

The archive is versioned by `format_version` (currently `4`, `FORMAT_VERSION` in
`web/services/contest_backup_service/models.py`). This server restores versions
**1, 2, 3, and 4**; anything else is refused. Bump it on any breaking layout change and
update this document.

### Version 4: the stored announcement flag

Version 4 adds the `is_announcement` column to every row in `clarifications.json`.
It is `NOT NULL` in the live table, and strict row validation compares archived rows
with that table, so a v4 archive that omits the key is refused as malformed rather
than quietly defaulted to `false` — which would demote every announcement it holds.

Versions 1 to 3 predate the column and may omit it. Such an archive carries exactly
one signal about the question: the role its own `users.json` recorded for the row's
author, which is the rule the application applied until the column landed. So the
rule here is the same **explicit wins, infer only on absence** the strategy uses, and
for the same reason — it is written once, in
`web/services/contest_backup_service/announcement.py`, because both the integrity
checker and the restorer consult it, and an archive must not validate as one kind of
row and restore as another. An archive labelled 1-3 that *does* state the flag
(captured after the column landed, before this bump) keeps what it states.

The per-team read markers in `clarification_reads` are deliberately **not** archived.
They are per-team UI state, not contest content, and their absence means "unread",
which is the safe default for a restored contest: every announcement it carries shows
as new to the restored teams.

### Version 3: problem editorials

Version 3 adds the nullable `editorial` column to every problem row in
`problems.json`. Strict row validation compares archived rows with the live
table, so version 3 requires the column even when its value is `null`.

Versions 1 and 2 predate the column. Their problem rows may omit `editorial`, in
which case restore stores `NULL`. Embedded problem packages remain package
format version 2 and may carry the additive `editorial` object and
`editorial.md`; the database row remains the restore source of record.

### Version 2: the stored validation strategy

Version 2 archives carry each problem's `validator_type` and `artifact_generation`
in the `problems.json` payload rows, and embed **version 2 problem packages**. Both
columns are mandatory on this branch: a v2 archive that omits one is refused as
malformed rather than quietly filled in by inference.

### Restoring version 1

Row validation compares each row against the **live** table, so a column added to
`problems` becomes one every archive is expected to carry. On the v1 branch,
`validator_type`, `artifact_generation`, and `editorial` are therefore optional;
the fence takes its server default of `0`. Version 2 requires the strategy and
fence but treats only `editorial` as optional.

There are **two v1 shapes in the wild**, and conflating them corrupts data:

| Shape | Captured | Carries `validator_type` |
| --- | --- | --- |
| Pre-strategy | before the column existed | no |
| Interim | after the column landed, before this format bump | **yes** |

The rule is therefore **explicit wins, infer only on absence**
(`web/services/contest_backup_service/strategy.py`):

- a v1 row that *states* the strategy restores with that value verbatim;
- a v1 row that omits it falls back to the legacy inference — a custom-validator
  row in the same archive means `interactive`, otherwise `standard`, never
  `checker`.

Treating "version 1" as "always infer" would corrupt precisely the interim
archives: a standard problem carrying a stale validator row would restore as
interactive. That is the corruption this release exists to eliminate.

The same predicate also decides whether a `problems/NNN/out/NNN.out` payload
member is required — expected output is required by the *strategy*, not by whether
a validator row happens to hold active source. One helper serves both callers so
an archive cannot validate under one strategy and restore under another.

### Why new archives carry the strategy

Stripping it to keep an archive literally v1 was considered and rejected: it is
lossy in exactly the cases this release exists to fix — a standard problem
carrying a stale validator row would restore as interactive, and an interactive
problem whose source was removed has nothing to infer from and would restore as
standard. Silent strategy corruption on a round trip is worse than the
forward-compatibility cost, which is that an archive written by this version is
rejected by an older server with a clear error rather than restored wrongly.

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
