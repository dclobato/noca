# Phase 3 — Version packages and backups

Carry the stored strategy across both serialization formats, bumping each to
version 2 while keeping version 1 readable.

Depends on Phase 2 (the strategy must be stored before it can be serialized).
Authoritative detail: `docs/problem-editor/PLAN.md`, "Phase 2 — Adopt problem-package
format version 2".

## Problem package v2

`shared/services/problem_package/` is the single place format decisions are
made; keep it that way so Web and Arena cannot interpret a package differently.

- `constants.py`: `FORMAT_VERSION` 1 → 2 (currently line 26).
- `metadata.py::check_format_version`: accept a missing version and explicit `1`
  as legacy v1, plus explicit `2`; return the version.
- `model.py::PackageMetadata`: add `validator_type: ProblemValidatorType`,
  beside `format_version` and `custom_validator` — that dataclass is the parsed
  `problem.json` contract and is where the discriminator belongs.
- `metadata.py::parse_metadata`:
  - **v1** — derive: `interactive` when `custom_validator` is present, else
    `standard`. Never `checker`.
  - **v2** — require the discriminator and enforce what `problem.json` alone can
    see: `standard` requires `custom_validator: null`; `interactive` requires a
    non-null `custom_validator`. Unknown values and inconsistent combinations
    fail before persistence.
  - `checker` parses but is **rejected centrally here / in the reader**, raising
    before any `ProblemPackage` is returned, with an explicit "output checker
    validation is not available in this build". The two domain importers never
    implement this check themselves, so they cannot diverge. Do not silently
    downgrade it, and do not import it as a disabled draft.
- `reader.py::_read_validator` (line 164): the archive-index half of the rule
  lives here, not in metadata parsing, because only the reader holds the
  extracted member index. It already raises when `validator/` members appear
  with no `custom_validator` declaration; extend it so a `standard` declaration
  shipping `validator/` members is refused with the same clarity.
- `writer.py`: emit `format_version: 2` and `validator_type`.
  `checker_semantics` stays out of scope until the checker itself lands. Public
  exports remain non-importable and carry no strategy metadata or validator
  source.

Both importers (`web/services/problem_service/importing.py`,
`arena/services/admin_problem_io_service.py`) set the new problem's
`validator_type` from the normalized metadata. Only v2 is written: Standard full
exports write `custom_validator: null`; Interactive full exports require a
complete source declaration. **Refuse** a full export of an incomplete
Interactive draft, with a clear operator-facing error, rather than emitting a
package that violates the v2 rules.

## Contest backup v2

This is the half most likely to be under-scoped — the version bump is
necessary, and there is a second effect that breaks existing archives:

- `validate_manifest` (`web/services/contest_backup_service/validation.py:167`)
  rejects any `format_version` that is not exactly the current constant, so a
  new archive carrying `validator_type` while still labelled v1 is a breaking
  change mislabelled as compatible.
- `validate_row` (`row_validation.py:46`) validates each row against the live
  `Table`'s column set. Adding `validator_type` to `problems` therefore makes
  strict validation **expect** that column — so **existing v1 archives stop
  restoring the moment Phase 2's column lands**, independent of what new exports
  write.

So:

1. Bump the backup `FORMAT_VERSION` to 2.
2. The restorer accepts both 1 and 2.
3. On the **v1 branch only**, pass `validator_type` through `optional_columns`
   and fill it by legacy inference (custom validator present ⇒ `interactive`,
   otherwise `standard`, never `checker`).
4. v2 archives carry the strategy explicitly in both the payload rows and their
   embedded v2 problem packages.
5. Do not compile restored validator data during restore unless the existing
   restore contract already requires it.

## Also update in this phase

- `docs/PROBLEM_PACKAGE_FORMAT.md`.
- The rendered `shared/template/_partials/problem_package_format.html` reference
  shown on both import pages.
- The sample packages served by `download_sample_problem_package` and
  `arena_admin_problem_sample_package`.
- `docs/CONTEST_BACKUP_FORMAT.md` — v2, strategy in the payload, v1 legacy
  normalization.

## Tests

- Missing-version and v1 packages import with the legacy mapping.
- v2 Standard and Interactive packages round-trip **across modules** (export
  from one, import into the other).
- v2 with a missing or invalid strategy, and inconsistent source/member
  combinations, fail before persistence.
- `checker` fails with the explicit unsupported message, raised once in the
  shared layer — assert both importers surface the same error.
- Incomplete Interactive full export fails clearly.
- **A v1 backup archive captured before Phase 2 still restores**, with the
  strategy inferred. This is the regression that catches the `validate_row`
  trap.
- A v2 backup round-trips with the strategy explicit.
- An unknown backup version is still refused.

## Gate

Both formats round-trip at v2, every v1 artifact — package *and* backup —
still imports and restores, and cross-module round-trips agree.
