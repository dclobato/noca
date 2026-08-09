# Problem Package Format

NOCA problems move between systems as a **plain ZIP archive** (deflate or stored). The same
format is used by the **Contest (web)** and **Arena** modules, and both consume the *same*
subsystem — `shared/services/problem_package/` — so a package built on one side reads identically
on the other. Every format decision (integer coercion, null semantics, string lengths, UTF-8,
image resolution, archive safety, export field sets) is made there exactly once; a domain
importer only decides what its own schema can store.

This document is the canonical reference for **format version 1**. The UI on the import pages is
rendered from `shared/template/_partials/problem_package_format.html`, the downloadable sample ZIP
is built by `shared/services/sample_problem_package.py`, and
`scripts/validate_problem_package.py` checks a package offline with the same reader.

Related references:
- [CUSTOM_VALIDATOR.md](CUSTOM_VALIDATOR.md) — interactive-problem lifecycle and verdict protocol
- [SHARED_SERVICES.md](SHARED_SERVICES.md) — the `problem_package` subsystem, staging, and the import journal
- [../shared/services/problem_package/](../shared/services/problem_package/) — reader, writer, staging, journal
- [../shared/services/sample_problem_package.py](../shared/services/sample_problem_package.py) — the reference "A + B" package
- [../shared/services/problem_image.py](../shared/services/problem_image.py) — image caps and MIME maps
- [../shared/services/custom_validator.py](../shared/services/custom_validator.py) — packaged validator lifecycle

## Package profiles

A package is written in one of two profiles, and they are **not** interchangeable.

| Profile | Contains | Importable |
| --- | --- | --- |
| `full` | `problem.json` with every version-1 key, statement, image, **all** test cases with explanations, sample interactions, validator source | **Yes** |
| `public` | statement, image, **public** test cases with their explanations, sample interactions | **No** |

The `public` profile is a **contestant-facing statement bundle**. It deliberately carries no
`problem.json`, no secret test case, no limits, no notes, and no validator source, so it cannot be
re-imported — feeding one to an importer fails with `problem.json not found in ZIP.` That is the
intended behavior, not a defect.

Public bundles are served by `GET /c/{slug}/problems/{label}/export` (Contest) and
`GET /problems/{arena_number}/export` (Arena); both are participant routes. Full packages come
from the admin export routes.

## Format version

```json
{ "format_version": 1 }
```

Every package written by this build carries `format_version: 1`. On import:

- an **absent** key means version 1, the format that predates the key;
- **any other value** fails *before* any other metadata is interpreted, with a message naming the
  supported version.

Checking the version first is deliberate: interpreting half of a package's metadata under
assumptions the package never agreed to is worse than refusing it.

## Directory layout

The package is rooted at the archive root — there is **no enclosing top-level directory**. Files
live at the root or in one of the reserved subfolders `in/`, `out/`, `explanation/`,
`validator/`, `interaction/`.

### Layout produced by Arena exports

```
/
├── problem.json         ← required
├── statement.md         ← required
├── image.<ext>          (optional — gif, png, jpg, jpeg or webp)
├── validator/           (optional — interactive problems)
│   └── validator.py     ← e.g. validator.py, validator.cpp
├── interaction/         (optional — interactive problems only)
│   ├── 001.interaction
│   ├── 001.explain      (optional)
│   └── ...
├── in/
│   ├── 001.in
│   ├── 002.in
│   └── ...
├── out/
│   ├── 001.out
│   ├── 002.out
│   └── ...
└── explanation/         (optional)
    ├── 001.txt
    └── ...
```

### Layout produced by Contest (web) exports

```
/
├── problem.json                    ← required
├── statement.md or statement.pdf   ← one required
├── image.<ext>                     (optional — gif, png, jpg, jpeg or webp)
├── validator/                      (optional — interactive problems)
│   └── validator.py                ← e.g. validator.py, validator.cpp
├── interaction/                    (optional — interactive problems only)
│   ├── 001.interaction
│   ├── 001.explain                 (optional)
│   └── ...
├── in/
│   ├── 001.in
│   ├── 002.in
│   └── ...
├── out/
│   ├── 001.out
│   ├── 002.out
│   └── ...
└── explanation/                    (optional)
    ├── 001.txt
    └── ...
```

### Flat test-case layout

A flat layout is accepted on import — `001.in` / `001.out` (or `001.sol`) directly at the package
root. Exports always use the directory layout.

**A package must not mix the two.** An archive holding both `001.in` and `in/002.in` is refused:
the two layouts share one ordinal space, and quietly picking one loses whichever cases were
written in the other.

## Archive safety

Nothing is written to disk until the whole archive has been accepted. The reader refuses:

- absolute paths, `..` traversal, backslash separators, duplicate separators (`//`);
- symbolic-link entries;
- **encrypted** entries;
- compression methods other than *stored* and *deflate*;
- members that collide exactly, case-insensitively, or after Unicode NFC normalization;
- two files claiming the same logical test-case stream (`out/001.out` and `out/001.sol`, or
  `001.in` and `in/001.in`);
- mixed flat and directory layouts.

`__MACOSX/` entries and `.DS_Store` files are dropped with a warning. Unrecognized but *safe*
members are ignored, so a newer producer's extra files do not make a package unreadable.

### Ceilings

| Limit | Value |
| --- | --- |
| Compressed upload | 256 MiB |
| Archive members | 10 000 |
| Single member (uncompressed) | 64 MiB |
| Total uncompressed | 512 MiB |
| `statement.pdf` | 32 MiB |
| `statement.md` | 512 KiB |
| Each explanation | 512 KiB |
| Each interaction transcript / explanation | 512 KiB |
| Illustration image | 2 MiB |
| Validator source | 256 KiB |
| Test cases | 1000 |
| Test-case ordinal | 1..1000 |

The compressed ceiling is enforced **while the upload streams**, so an oversized package is
refused without ever having been buffered whole.

There is deliberately **no compression-ratio ceiling**. The per-member and aggregate uncompressed
caps already bound extraction, and legitimate test-case data is often repetitive enough to exceed
any ratio a decompression bomb would.

## `problem.json` fields

`problem.json` is a single JSON object at the package root. A `full` export writes **every**
version-1 key, including keys the exporting domain cannot store — as `null`, `false`, or `{}`
rather than omitted, so a consumer never has to guess whether absence means "unset" or
"unsupported by the producer".

```json
{
  "format_version": 1,
  "title": "A + B",
  "author": "John Doe",
  "notes": "Internal management note.",
  "source": "ICPC 2025",
  "license": "CC BY-SA 4.0",
  "color": "#4287f5",
  "hide_author_show_source": false,
  "statement_language": "en",
  "time_limit_ms": 1000,
  "memory_limit_kb": 262144,
  "pids_limit": 64,
  "output_limit_in_bytes": 65536,
  "categories": ["sample", "math"],
  "sample_testcases": [1, 3],
  "image": "image.png",
  "image_caption": "A red square.",
  "language_limits": {
    "python3": {
      "time_limit_ms": 3000,
      "memory_limit_kb": 262144,
      "pids_limit": 64,
      "output_limit_in_bytes": 1048576,
      "repetitions": 3
    }
  },
  "custom_validator": {
    "language_id": "cpp20",
    "source_file": "validator/validator.cpp"
  },
  "sha256": {
    "statement.md": "…64 hex chars…",
    "in/001.in": "…"
  }
}
```

### Fields

| Field | Type | Required | Default when absent | Description |
| --- | --- | --- | --- | --- |
| `format_version` | integer | no | `1` | Must be `1` when present. |
| `title` | string | **yes** | — | Non-empty after trim. Max **256** characters. |
| `author` | string \| null | no | `null` | Free-text authorship, max **256**. On Arena, absent means the importing user is recorded as both owner and author. |
| `notes` | string \| null | no | `null` | Internal management note, max **512**. |
| `source` | string \| null | no | `null` | Origin of the problem, max **256**. Stored by Arena only; always parsed and always exported. |
| `license` | string \| null | no | `null` | License shown on the public problem page, max **256**. Arena only. |
| `color` | `#rrggbb` \| null | no | `null` | Balloon color. Stored by Contest only; always parsed and always exported. When absent, a Contest import picks an unused `BALLOON_COLORS` entry. |
| `hide_author_show_source` | boolean | no | `false` | Show `source` instead of the author name. Arena only. |
| `statement_language` | `"pt"`\|`"en"`\|`"es"` \| null | no | `null` | Natural language of the statement. Arena only; an absent value triggers detection, which the importer is asked to confirm. |
| `time_limit_ms` | integer ≥ 1 | no | `1000` | Per-test-case time limit. |
| `memory_limit_kb` | integer ≥ 1 | no | `262144` | cgroup memory limit in KiB. |
| `pids_limit` | integer ≥ 1 | no | `64` | cgroup `pids` limit. |
| `output_limit_in_bytes` | integer ≥ 1 | no | `65536` | Max stdout bytes. **Never null**: both problem tables make the column NOT NULL. |
| `categories` | array of strings | no | `[]` | Sequence preserved in the package. |
| `sample_testcases` | array of integers | no | `[]` | Which **source** ordinals are public. See below. |
| `image` | filename string \| null | no | auto-detect | When set, the named member must exist. |
| `image_caption` | string \| null | no | `null` | Max **512** characters. |
| `language_limits` | object | no | `{}` | Per-language overrides. Stored by Contest only. |
| `custom_validator` | object \| null | no | `null` | Declares an interactive validator. |
| `sha256` | object | no | `{}` | Integrity manifest. **Mandatory on new full exports**, optional on import. |

Field widths are unified across both domains, so a value that survives on one side survives on
the other. Strings are **type-checked**: a JSON number for `notes` is an error, not `"42"`.

### Null versus absent

A **nullable** field (`author`, `notes`, `source`, `license`, `color`, `statement_language`,
`image`, `image_caption`, `custom_validator`) accepts an explicit `null`, which means the same as
omitting it.

A field that has an empty value of its own — `categories`, `sample_testcases`, `language_limits`,
`sha256`, `hide_author_show_source`, `format_version`, and the four limits — does **not**. `null`
there is a statement the format cannot express, so it is reported rather than quietly read as
absence: write `[]`, `{}`, `false`, or omit the key.

### Number handling

`time_limit_ms`, `memory_limit_kb`, `pids_limit`, and `output_limit_in_bytes` must be integers
`>= 1`. JSON integers and trimmed decimal strings (`"1500"`) are accepted. Rejected, with the
offending value quoted in the message: floats, booleans, zero, negatives, and an explicit `null`
— a package that states a limit as null is stating something the format cannot express.

### `sample_testcases`

```json
"sample_testcases": [1, 3]
```

Unique, **strictly ascending** ordinals in the package's *own* (source) ordinal space, validated
against the set of cases the package actually carries **before** the contiguous remap, then
applied as the imported cases' `is_sample` flag.

This is what makes the `explanation/` folder meaningful: explanations render next to sample cases,
so a package with no public cases shows a contestant no worked examples at all.

**It must be empty for an interactive problem.** Such a problem presents sample interactions
instead of sample test cases, and the zero-public-cases invariant is enforced by the reader rather
than left to each domain to remember.

### `sha256`

```json
"sha256": { "statement.md": "…", "in/001.in": "…", "out/001.out": "…" }
```

Lowercase 64-character hex digests over the **exact raw bytes stored in each ZIP member**,
computed *before* test-case newline normalization, so a package can be verified without
interpreting what its members mean.

The map covers every recognized payload member **except `problem.json` itself**, which would
otherwise have to hash the file containing its own digest.

- A **present** map must cover exactly the recognized payload set and match the streamed contents.
  Missing entries, extra entries, and mismatches are all hard errors.
- An **absent** map imports with an `integrity_manifest_missing` warning, because older packages
  predate the manifest.

### `language_limits`

Keyed by judge language id (`"python3"`, `"cpp20"`, `"rust"`). Stored by Contest only; Arena has
no per-language overrides and writes `{}`.

| Sub-field | Type | Required | Description |
| --- | --- | --- | --- |
| `time_limit_ms` | integer ≥ 1 | **yes** | Time limit override. |
| `memory_limit_kb` | integer ≥ 1 | **yes** | Memory limit override. |
| `pids_limit` | integer ≥ 1 | **yes** | PIDs limit override. |
| `output_limit_in_bytes` | integer ≥ 1 \| null | no | **May be omitted or null, meaning inherit the problem's limit.** |
| `repetitions` | integer ≥ 1 | no | Profiling repetitions. Absent takes the target language registry's default, which only the importing side knows. |

`output_limit_in_bytes` stays nullable here — and only here. `problem_language_limits` keeps a
nullable column whose NULL means "inherit", which is exactly what the judge's
`coalesce(per-language, problem)` computes.

On import, entries naming a language the target contest does not allow are dropped, and the
importer is told which ones in a `disallowed_language_limits` warning.

## Test cases

### File naming

Matched case-insensitively:

- Directory layout: `^(in|out)/0*([1-9]\d{0,3})(\.in|\.out|\.sol)?$`
- Flat layout: `^0*([1-9]\d{0,3})\.(in|out|sol)$`

Rules:

- Leading zeros are stripped, so `001.in` and `1.in` name the same ordinal.
- `.out` and `.sol` are interchangeable — but **not both for the same ordinal**.
- The patterns accept four digits and the reader **separately rejects any ordinal outside
  1..1000**, so a package holding ordinals `1` and `5000` fails loudly instead of quietly
  importing two cases.
- Maximum 1000 test cases.

### Pairing and ordering

- Inputs and outputs pair by ordinal; unpaired ordinals on either side are an error naming them.
- The pairing rule does **not** apply to an interactive problem: its cases are input-only, and
  output files are ignored with a warning.
- Ordinals are **remapped contiguously from 1**, regardless of gaps in source filenames.

```
in/001.in   out/001.out
in/002.in   out/002.out
in/007.in   out/007.sol       ← .sol accepted
in/010.in   out/010.out
                              ↑ remap to 1, 2, 3, 4 on import
```

### Content rules

- Every kept input and output must be valid **UTF-8**. Binary test cases are rejected in **both**
  domains — the previous Contest path accepted them only because nothing on that side checked.
- CRLF and lone CR are normalized to LF **after** hashing, so the integrity manifest describes
  what the package shipped rather than a derived form.
- Empty content and empty lines are preserved.
- No per-file size cap beyond the archive ceilings. The 10 KiB `MAX_INLINE_TESTCASE_BYTES` is a
  textarea-editing gate only.

### On-disk layout after import

```
<NOCA_PROBLEM_TESTCASE_DIR>/<contest|arena>/<problem_id>/NNN.in
<NOCA_PROBLEM_TESTCASE_DIR>/<contest|arena>/<problem_id>/NNN.out
```

`NNN` is `ordinal:03d`. Files are staged next to their final location and promoted with a
same-filesystem rename before the transaction commits; see
[SHARED_SERVICES.md](SHARED_SERVICES.md) for the journal that makes a crash between those two
steps recoverable.

### Public vs. secret

`sample_testcases` decides. Cases it does not name are secret. An interactive problem has **zero**
public cases and at least one secret one.

## `explanation/` folder

Author notes explaining why a test case has its expected output. They render **below the sample**
on the problem page, so an explanation for a secret case is stored but never shown.

```
explanation/
├── 001.txt    ← optional UTF-8 text for source ordinal 1
└── ...
```

- Regex: `^explanation/0*([1-9]\d{0,3})\.txt$` (case-insensitive).
- Matched by source ordinal, then remapped with its case.
- UTF-8 only, max 512 KiB.
- Whitespace-only explanations become `null`.
- An explanation with no matching test case is dropped with an `orphan_explanation` warning.

## Statement

One of:

- `statement.md` — Markdown (UTF-8). Required on Arena. Preferred over PDF when both are present.
- `statement.pdf` — PDF. Contest only.

### Markdown validation

Through `validate_md_content` (`shared/problem_statement_markdown.py`): valid UTF-8, at most
512 KiB, and none of `link` (any Markdown link, autolink, reference link, or bare URL), `image`
(`![alt](src)`), or `html` (blocks and inline). LaTeX (`$...$`) and mermaid fenced blocks are
allowed.

### PDF validation

1. the `%PDF-` signature;
2. the 32 MiB cap;
3. `pypdf.PdfReader(path, strict=False)` — the document must **not be encrypted** and must have
   **at least one readable page**.

Non-strict parsing is deliberate: a PDF produced by LaTeX or Word may violate minor points of the
specification while rendering correctly in every viewer, and refusing those would reject
legitimate statements. What is refused is a file that is not a PDF, is encrypted, or has no
readable page.

## Illustration image

| Extension | MIME type |
| --- | --- |
| `gif` | `image/gif` |
| `png` | `image/png` |
| `jpg` / `jpeg` | `image/jpeg` |
| `webp` | `image/webp` |

- Maximum **2 MiB**, maximum **2048 × 2048 pixels**. The fixed dimensions keep packages portable
  between deployments with different general image settings.
- Validated and re-encoded through `ImageProcessingService`. Animated GIF frames and timing are
  preserved.
- When `problem.json.image` names a file, that file **must exist** in the archive. When the key is
  absent, the first root-level member with a recognized extension is used.
- `image_caption` is shown below the image; whitespace-only captions become `null`.
- The image is part of the statement a contestant reads, so it ships in **both** profiles.

## Interactive problems (`validator/`)

A custom validator makes a problem **interactive**: the contestant's program talks to a validator
that decides the verdict. See [CUSTOM_VALIDATOR.md](CUSTOM_VALIDATOR.md) for the lifecycle and
protocol.

```
validator/
└── validator.py     ← UTF-8 validator source
in/
├── 001.in           ← inputs only; no out/ directory
└── 002.in
```

```json
"custom_validator": { "language_id": "cpp20", "source_file": "validator/validator.cpp" }
```

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `language_id` | string | **yes** | Must be an active judge language on the *target* install. Checked at import, before anything commits — not by the reader, which cannot know. |
| `source_file` | string | **yes** | Must be of the exact form `validator/<name>`, a single path segment. Nested paths and traversal are refused, and so is naming a member outside `validator/` — otherwise `statement.md` could be declared as the validator source. |

Rules:

- Source must be valid UTF-8, non-empty, non-blank, at most 256 KiB.
- The declared member must exist in the archive.
- The **language id is authoritative**. A `source_file` extension that disagrees with it produces
  a `validator_extension_mismatch` warning, not an error.
- `validator/` members with **no** `custom_validator` metadata are a **hard error**: shipping a
  validator that silently does not get configured is exactly the quiet data loss the format
  refuses.
- Each case's input is fed to the validator's stdin before the conversation starts, so it
  parametrizes one round rather than declaring an expected answer. Output files present in the
  archive are ignored with a warning.
- Every case is secret; `sample_testcases` must be empty.
- The validator is staged as a `PENDING` candidate and a compile job is enqueued **after** the
  transaction commits. The problem accepts no submissions until it is promoted.
- `full` exports carry the validator source; `public` bundles never do.

## `interaction/` folder

An interactive problem's public examples are **sample interactions** — worked transcripts of the
conversation the contestant's program will have.

```
interaction/
├── 001.interaction   ← required: the transcript, as JSON
├── 001.explain       ← optional: plain-text explanation
└── ...
```

`NNN` reflects the author's ordering; gaps are closed on import.

### `NNN.interaction`

One JSON object, in the same shape the judge records for a real interactive attempt
(`submission_interactive_attempts.transcript`), so one renderer serves both:

```json
{
  "lines": [
    {"dir": "validator", "line": "3"},
    {"dir": "user", "line": "5"},
    {"dir": "validator", "line": "!8"}
  ],
  "truncated": false
}
```

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `lines` | array | **yes** | Non-empty, ordered conversation. |
| `lines[].dir` | string | **yes** | `"validator"` or `"user"`. |
| `lines[].line` | string | **yes** | The protocol line, without its trailing newline. |
| `truncated` | bool | no | Defaults to `false`. |

In the admin UIs the same transcript is authored as plain text with the exact two-character
prefixes `> ` (validator speaking) and `< ` (contestant speaking):

```
> 3
< 5
> !8
```

The parser is strict: every line must carry one of those prefixes **including the space**. An
empty protocol line is written as `> ` with nothing after it.

### Rules

- **At most 5** per problem.
- Only meaningful with a validator: a package declaring none has its `interaction/` members
  dropped with an `interactions_dropped` warning.
- A validator package with no interactions imports successfully; the importer is warned the
  problem shows no examples.
- Exports carry only currently-visible interactions, renumbered without gaps.
- They ship in **both** profiles — unlike the validator source.

## Warnings vs. errors

The split is deliberate: anything that would lose data **silently** is an error; anything the
reader resolves on its own but the operator should know about is a structured warning, surfaced
as a flash on the import page.

Warnings: `macos_metadata`, `orphan_explanation`, `ignored_interactive_output`,
`interactions_dropped`, `unknown_categories`, `disallowed_language_limits`,
`integrity_manifest_missing`, `validator_extension_mismatch`.

Errors: `validator/` members with no `custom_validator` metadata; non-empty `sample_testcases` on
an interactive problem; a referenced file that is missing; any invalid recognized field; every
archive-safety violation above.

## Cross-domain preservation

The format is the **union** of both domains. Every key is always parsed and always exported; a
domain that cannot store one simply does not keep it. This replaces the older and too-broad claim
that "unknown keys are ignored" — unknown *members* are ignored, but a recognized key with an
invalid value is an error.

| Field | Contest stores | Arena stores | Lost in Contest → Arena | Lost in Arena → Contest |
| --- | --- | --- | --- | --- |
| `title`, `author`, `notes` | yes | yes | — | — |
| `time_limit_ms`, `memory_limit_kb`, `pids_limit`, `output_limit_in_bytes` | yes | yes | — | — |
| `categories` | yes (creates unknown) | yes (drops unknown) | unknown categories | — |
| `sample_testcases` | yes | yes | — | — |
| `image`, `image_caption` | yes | yes | — | — |
| statement, explanations, interactions, validator | yes | yes | — | — |
| `color` | **yes** | no | **`color`** | — (Contest picks one) |
| `language_limits` | **yes** | no | **`language_limits`** | — |
| `source`, `hide_author_show_source` | no | **yes** | — | **both** |
| `license` | no | **yes** | — | **`license`** |
| `statement_language` | no | **yes** | — | **`statement_language`** |
| PDF statement | **yes** | no | **the whole import fails** — Arena requires Markdown | — |

Anything not in the "lost" columns survives a round trip in either direction.

## Target-specific checks

Everything above is decided identically everywhere. These are **not**, because they depend on the
install a package is being imported into. A package the linter accepts can still be refused by one
of them, and that is correct behavior rather than drift:

| Check | Contest | Arena |
| --- | --- | --- |
| Validator `language_id` must be an active judge language | yes | yes |
| Categories | created on the fly | must already exist; unknown ones dropped with a warning |
| `language_limits` languages must be allowed by the contest | yes | n/a (no per-language limits) |
| Statement kind | Markdown or PDF | Markdown only |

## Offline validation

```bash
uv run python scripts/validate_problem_package.py PATH
```

Runs the same reader both importers use: exit 0 with any warnings printed, exit 1 with one
actionable error. It cannot check anything that depends on the target install — whether the
categories exist, whether the validator's language is enabled, whether the contest allows the
languages named in `language_limits`. Those are decided at import.

## Constants at a glance

| Constant | Value | Defined in |
| --- | --- | --- |
| `FORMAT_VERSION` | 1 | `shared/services/problem_package/constants.py` |
| `MAX_UPLOAD_BYTES` | 256 MiB | `shared/services/problem_package/constants.py` |
| `MAX_ARCHIVE_MEMBERS` | 10 000 | `shared/services/problem_package/constants.py` |
| `MAX_MEMBER_UNCOMPRESSED_BYTES` | 64 MiB | `shared/services/problem_package/constants.py` |
| `MAX_TOTAL_UNCOMPRESSED_BYTES` | 512 MiB | `shared/services/problem_package/constants.py` |
| `MAX_PDF_BYTES` | 32 MiB | `shared/services/problem_package/constants.py` |
| `MAX_TITLE_CHARS` / `MAX_AUTHOR_CHARS` | 256 | `shared/services/problem_package/constants.py` |
| `MAX_NOTES_CHARS` / `MAX_IMAGE_CAPTION_CHARS` | 512 | `shared/services/problem_package/constants.py` |
| `MAX_SOURCE_CHARS` / `MAX_LICENSE_CHARS` | 256 | `shared/services/problem_package/constants.py` |
| `DEFAULT_OUTPUT_LIMIT_BYTES` | 65 536 | `shared/services/problem_package/constants.py` |
| `MAX_TEST_CASES` / `MAX_TEST_CASE_ORDINAL` | 1000 | `shared/services/problem_package/constants.py` |
| `MAX_INLINE_TESTCASE_BYTES` | 10 KiB | `shared/tc_zip.py` (textarea gate only) |
| `MAX_PROBLEM_IMAGE_BYTES` | 2 MiB | `shared/services/problem_image.py` |
| `MAX_PROBLEM_IMAGE_WIDTH` / `_HEIGHT` | 2048 px | `shared/services/problem_image.py` |
| `MAX_CUSTOM_VALIDATOR_SOURCE_BYTES` | 256 KiB | `shared/services/custom_validator.py` |
| `MAX_SAMPLE_INTERACTIONS` | 5 | `shared/services/sample_interactions.py` |
| Statement Markdown cap | 512 KiB | `shared/problem_statement_markdown.py` |

## End-to-end examples

### The "A + B" sample package

```
noca-sample-problem-a-plus-b.zip
├── problem.json
├── statement.md
├── in/001.in    out/001.out    explanation/001.txt   ← public
├── in/002.in    out/002.out
└── in/003.in    out/003.out
```

`problem.json` (abridged — a real export also carries the `sha256` map):

```json
{
  "format_version": 1,
  "title": "A + B",
  "author": "John Doe",
  "notes": "Sample problem",
  "source": "NOCA documentation",
  "license": "cc sa-by",
  "color": "#4287f5",
  "hide_author_show_source": false,
  "statement_language": "en",
  "time_limit_ms": 1000,
  "memory_limit_kb": 262144,
  "pids_limit": 64,
  "output_limit_in_bytes": 1048576,
  "categories": ["sample", "math"],
  "sample_testcases": [1],
  "image": null,
  "image_caption": null,
  "language_limits": {
    "python3": {"time_limit_ms": 3000, "memory_limit_kb": 262144, "pids_limit": 64,
                "output_limit_in_bytes": 1048576, "repetitions": 3},
    "rust":    {"time_limit_ms": 1000, "memory_limit_kb": 131072, "pids_limit": 32,
                "output_limit_in_bytes": 1048576, "repetitions": 1}
  },
  "custom_validator": null
}
```

`statement.md`:

````markdown
# A + B

Read two integers and print their sum.

## Input

A single line with two integers, `a` and `b`, separated by a space.

## Output

A single line with the value of `a + b`.
````

`in/001.in` is `1 2`, `out/001.out` is `3`, and `explanation/001.txt` reads *"The two numbers on
the input line are added together."* This is exactly what `build_sample_problem_package()` writes
and the import pages serve.

### Contest package with a PDF statement and an image

```
my-contest-problem.zip
├── problem.json
├── statement.pdf
├── image.png
├── in/001.in
└── out/001.out
```

```json
{
  "format_version": 1,
  "title": "Geometry Maze",
  "author": "Jane Doe",
  "notes": "Created for the 2025 regionals.",
  "color": "#00FFFF",
  "time_limit_ms": 2000,
  "memory_limit_kb": 262144,
  "pids_limit": 64,
  "output_limit_in_bytes": 1048576,
  "categories": ["geometry", "graph"],
  "sample_testcases": [1],
  "image": "image.png",
  "image_caption": "Figure 1. The maze layout.",
  "language_limits": {
    "cpp20": {"time_limit_ms": 2000, "memory_limit_kb": 262144, "pids_limit": 64,
              "output_limit_in_bytes": 1048576, "repetitions": 1}
  }
}
```

This package **cannot** be imported into Arena, which requires a Markdown statement.

### Interactive problem

```
number-guessing.zip
├── problem.json
├── statement.md
├── validator/validator.py
├── interaction/001.interaction
├── interaction/001.explain
├── in/001.in       ← the round the validator plays; no out/ directory
└── in/002.in
```

```json
{
  "format_version": 1,
  "title": "Number Guessing",
  "author": "Jane Doe",
  "source": "ICPC 2025",
  "license": "CC BY-SA 4.0",
  "statement_language": "en",
  "time_limit_ms": 2000,
  "memory_limit_kb": 262144,
  "pids_limit": 64,
  "output_limit_in_bytes": 65536,
  "categories": ["interactive", "search"],
  "sample_testcases": [],
  "custom_validator": {"language_id": "python3", "source_file": "validator/validator.py"}
}
```

`validator/validator.py` — it reads the test case first, then plays that round:

```python
import sys

# The test case: the secret to guess and how many queries the contestant gets.
secret, budget = (int(value) for value in sys.stdin.readline().split())

for _ in range(budget):
    line = sys.stdin.readline()      # now the contestant is talking
    if not line:
        sys.exit(1)                  # contestant went away -> WA
    line = line.strip()
    if line.startswith("!"):
        guess = int(line[1:].strip())
        sys.exit(0 if guess == secret else 1)
    if line.startswith("?"):
        x = int(line[1:].strip())
        print("<" if x > secret else ">", flush=True)
        continue
    sys.exit(4)                      # malformed message -> PE
sys.exit(2)                          # out of queries -> TLE
```

`in/001.in` is `42 50` (secret 42, 50 queries); `in/002.in` is `999999 20`. The judge runs the
validator once per case, in order, feeding it that case's line before the contestant says
anything. The submission is Accepted only if every round ends with exit `0`.

### Flat-layout package

```
flat-problem.zip
├── problem.json
├── statement.md
├── 001.in
├── 001.out
├── 002.in
└── 002.sol       ← .sol accepted as .out
```

This parses identically to the directory-layout version. Adding an `in/003.in` to it would be
refused as a mixed layout.
