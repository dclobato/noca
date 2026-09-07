# Problem Package Format

NOCA problems move between systems as a **plain ZIP archive** (deflate or stored). The same
format is used by the **Contest (web)** and **Arena** modules, and both consume the *same*
subsystem — `shared/services/problem_package/` — so a package built on one side reads identically
on the other. Every format decision (integer coercion, null semantics, string lengths, UTF-8,
image resolution, archive safety, export field sets) is made there exactly once; a domain
importer only decides what its own schema can store.

This document is the canonical reference for **format version 2**, and for reading the version 1
packages this build still accepts. The UI on the import pages is
rendered from `shared/template/_partials/problem_package_format.html`, the downloadable sample ZIP
is built by `shared/services/sample_problem_package.py`, and
`scripts/validate_problem_package.py` checks a package offline with the same reader.

Related references:

- [Default token validator](custom-validator/TOKEN_VALIDATOR.md) — standard
  expected-output and comparison behavior
- [Interactive validator guide](custom-validator/INTERACTIVE_VALIDATOR.md) —
  interactive-problem lifecycle and verdict protocol
- [SHARED_SERVICES.md](SHARED_SERVICES.md) — the `problem_package` subsystem, staging, and the import journal
- [../shared/services/problem_package/](../shared/services/problem_package/) — reader, writer, staging, journal
- [../shared/services/sample_problem_package.py](../shared/services/sample_problem_package.py) — the reference "A + B" package
- [../shared/services/problem_image.py](../shared/services/problem_image.py) — image caps and MIME maps
- [../shared/services/custom_validator.py](../shared/services/custom_validator.py) — packaged validator lifecycle

## Package profiles

A package is written in one of two profiles, and they are **not** interchangeable.

| Profile | Contains | Importable |
| --- | --- | --- |
| `full` | `problem.json` with every version-2 key, statement, optional editorial, image, **all** test cases with explanations, sample interactions, validator source | **Yes** |
| `public` | statement, image, **public** test cases with their explanations, sample interactions | **No** |

The `public` profile is a **contestant-facing statement bundle**. It deliberately carries no
`problem.json`, editorial, secret test case, limits, notes, or validator source, so it cannot be
re-imported — feeding one to an importer fails with `problem.json not found in ZIP.` That is the
intended behavior, not a defect.

Public bundles are served by `GET /c/{slug}/problems/{label}/export` (Contest) and
`GET /problems/{arena_number}/export` (Arena); both are participant routes. Full packages come
from the admin export routes.

## Format version

```json
{ "format_version": 3 }
```

Every package written by this build carries `format_version: 3`. On import:

- an **absent** key means version 1, the format that predates the key;
- explicit `1`, `2`, and `3` are all accepted;
- **any other value** fails *before* any other metadata is interpreted, with a message naming the
  supported versions.

Checking the version first is deliberate: interpreting half of a package's metadata under
assumptions the package never agreed to is worse than refusing it.

### What version 3 changes

Version 3 changes no keys at all. It changes what one existing number **means**:
`language_limits[].time_limit_ms` is now the limit for **one run** of a test case, where before it
was the budget shared by all of that case's repetitions. The problem's own top-level
`time_limit_ms` is unaffected — it is judged at exactly one repetition, so the two readings always
coincided there.

Because nothing in the file's shape moved, a version 1 or 2 package is still read in full; its
per-language time limits are **converted on import**, dividing by the repetition count that
applies to the entry and rounding up, so an import is never stricter than the package it came
from.

That conversion happens in the importing layer, not the parser, and the reason is
`repetitions` itself. An **omitted** `repetitions` means "use the target's registry default" — a
value the exporting side never knew. A version 2 package declaring `time_limit_ms: 1000` with no
`repetitions`, imported where the default is 10, has to be stored as `ceil(1000 / 10) = 100`;
storing 1000 would silently hand that language a 10,000 ms budget. Only the importing domain can
resolve that number, so only the importing domain can divide by it.

### What version 2 adds

Version 2 adds one key, `validator_type`, which states the problem's **validation strategy**
explicitly instead of leaving it to be inferred from whether a validator happens to be declared.
That inference was wrong in both directions: a problem whose validator source had been removed
looked standard, and a standard problem carrying a stale validator row looked interactive.

| Value | Meaning |
| --- | --- |
| `"standard"` | The built-in token comparator judges the submission against a fixed expected output. |
| `"interactive"` | A custom validator runs concurrently with the submission and decides the verdict. |
| `"checker"` | **Reserved.** Accepted by the vocabulary, but rejected on import — this build cannot judge output-checker problems. |

### Reading version 1

A version-1 package carries no strategy, so the reader derives one, and this is the last place
that derivation is still correct — it is all such a package says:

- a package declaring a `custom_validator` is read as `interactive`;
- any other package is read as `standard`;
- never `checker`.

A `validator_type` key appearing in a package that claims version 1 is **ignored**, not rejected.
The version the package declares is what decides how it is read.

### Version 2 consistency rules

Because version 2 states the strategy *and* still carries the validator declaration, the two can
disagree. A package where they do is refused before anything is persisted:

| Rule | Checked by |
| --- | --- |
| `"standard"` requires `"custom_validator": null` | `problem.json` alone (the metadata parser) |
| `"interactive"` requires a non-null `custom_validator` | `problem.json` alone (the metadata parser) |
| `"standard"` must ship no `validator/` members | the archive index (the reader) |
| `validator/` members require a `custom_validator` declaration | the archive index (the reader) |

The split is deliberate: only the reader holds the extracted archive index, so member-level rules
cannot live in metadata parsing.

`checker_semantics` is **not** part of version 2. It belongs to the output-checker strategy and
lands with it.

### Exporting

Only version 2 is written. A `full` export therefore refuses two problems it cannot represent:

- an **interactive problem with no validator source** — version 2 cannot express it, and writing it
  anyway would produce an archive this build's own reader rejects. Upload a validator source and
  export again;
- a **`checker` problem**, which this build cannot represent at all.

`public` bundles are unaffected: they carry no `problem.json`, so they carry no strategy metadata.

### Additive editorial metadata

Editorial support does not change the format version. The optional `editorial`
property and `editorial.md` member add content without changing the meaning of
any version-1 or version-2 field. Older readers already ignore unknown metadata
properties and safe unknown members.

The editorial digest is nested in the `editorial` object instead of the
top-level `sha256` manifest. This keeps the legacy manifest exact for older
version-2 readers, while updated readers still verify the editorial bytes.

`editorial.release_policy` is additive *within* that already-additive object, so
it too needs no version bump: a reader that does not know the property ignores
it, and a package that predates it parses with no policy at all. It is nested
rather than top-level because the policy only means something when there is an
editorial to release.

## Directory layout

The package is rooted at the archive root — there is **no enclosing top-level directory**. Files
live at the root or in one of the reserved subfolders `in/`, `out/`, `explanation/`,
`validator/`, `interaction/`.

### Layout produced by Arena exports

```
/
├── problem.json         ← required
├── statement.md         ← required
├── editorial.md         (optional — full packages only)
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
├── editorial.md                    (optional — full packages only)
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
| `editorial.md` | 512 KiB |
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
version-2 key, including keys the exporting domain cannot store — as `null`, `false`, or `{}`
rather than omitted, so a consumer never has to guess whether absence means "unset" or
"unsupported by the producer".

```json
{
  "format_version": 3,
  "validator_type": "standard",
  "title": "A + B",
  "author": "John Doe",
  "notes": "Internal management note.",
  "editorial": {
    "member": "editorial.md",
    "sha256": "…64 hex chars…",
    "release_policy": "after_ac"
  },
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
| `format_version` | integer | no | `1` | Must be `1`, `2`, or `3` when present. New exports write `3`. |
| `validator_type` | `"standard"`\|`"interactive"` | **yes on v2** | derived on v1 | The validation strategy. Required in version 2; derived from `custom_validator` presence in version 1, where the key is ignored if present. `"checker"` parses but is rejected as unsupported. |
| `title` | string | **yes** | — | Non-empty after trim. Max **256** characters. |
| `author` | string \| null | no | `null` | Free-text authorship, max **256**. On Arena, absent means the importing user is recorded as both owner and author. |
| `notes` | string \| null | no | `null` | Internal management note, max **512**. |
| `editorial` | object \| null | no | `null` | Declares the optional `editorial.md` solution guide, its independent SHA-256 digest, and the nested `release_policy` saying when it becomes visible to participants. |
| `source` | string \| null | no | `null` | Origin of the problem, max **256**. Stored by Arena only; always parsed and always exported. |
| `license` | string \| null | no | `null` | License shown on the public problem page, max **256**. Arena only. |
| `color` | `#rrggbb` \| null | no | `null` | Balloon color. Stored by Contest only; always parsed and always exported. When absent, a Contest import picks an unused `BALLOON_COLORS` entry. |
| `hide_author_show_source` | boolean | no | `false` | Show `source` instead of the author name. Arena only. |
| `statement_language` | `"pt"`\|`"en"`\|`"es"` \| null | no | `null` | Natural language of the statement. Arena only; an absent value triggers detection, which the importer is asked to confirm. |
| `expected_difficulty` | integer 1–100 \| null | no | `null` | The author's declared expected difficulty on the internal rating scale (display value ÷ 10); it seeds the Arena rating's prior. Arena only; always parsed and always exported. Booleans and out-of-range values are errors. |
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

A **nullable** field (`author`, `notes`, `editorial`, `source`, `license`, `color`,
`statement_language`, `expected_difficulty`, `image`, `image_caption`, `custom_validator`) accepts an
explicit `null`, which means the same as omitting it.

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

### `editorial`

The optional editorial is an official explanation and solution guide stored as
Markdown:

```json
"editorial": {
  "member": "editorial.md",
  "sha256": "…64 lowercase hex characters…",
  "release_policy": "after_ac"
}
```

The `member` value must be exactly `editorial.md`. The digest covers the exact
raw bytes stored in that member. Updated readers reject a missing declared
member, a digest mismatch, invalid UTF-8, content larger than 512 KiB, or
Markdown that violates the statement restrictions. An `editorial.md` member
without an `editorial` declaration is ignored with an `undeclared_editorial`
warning. This preserves compatibility with older or third-party packages that
used that safe filename for unrelated content without silently importing it.

Missing or `null` means the problem has no editorial. Full exports always write
either the object or `null`; public bundles never include `editorial.md`.

#### `editorial.release_policy`

States **when** the editorial becomes visible to participants.

| Value | Meaning |
| --- | --- |
| `"never"` | Editor-only. Participants never see it. |
| `"always"` | Visible to every participant on the problem page. |
| `"after_ac"` | Visible only once the participant has an Accepted verdict on the problem. |

- Absent or `null` means `"never"` — the column default, and the behavior of every
  package written before the property existed, so an older package imports unchanged.
- Any other value is a **hard error**, like every other recognized key with an invalid
  value. Whitespace is stripped before matching, so `"after_ac "` is accepted.
- Only **Arena** stores it (`arena_problems.editorial_release_policy`). Contest parses
  it and writes it back as `null`, because `problems` has no equivalent column — so the
  policy does not survive a round trip through a Contest problem, while the editorial
  text does.

Losing this on a round trip is not cosmetic: an editorial that lands on `never` is
invisible to participants, which silently undoes the author's decision on the importing
install.

### `expected_difficulty`

The author's expected difficulty, stored by Arena in `arena_problems.expected_difficulty`
and used as the mean of the rating's solve-rate prior, so a freshly imported problem starts
where its author expects rather than at the flat centre of the scale. The Arena editor
offers five worded anchors (Introductory 15, Easy 30, Standard 50, Challenging 70, Hard 85),
but the format accepts any integer in `[1, 100]` — a package hand-written with `42` imports
and rates fine, and simply shows no anchor word.

- Absent or `null` means no estimate: the importing problem uses the flat prior, exactly
  as every package written before the key existed. Additive within version 2, no bump.
- A boolean, a non-integer, or a value outside `[1, 100]` is a **hard error**.
- Only **Arena** stores it. Contest parses it and writes it back as `null`, because
  `problems` has no rating system — so the estimate does not survive a round trip through
  a Contest problem.

### `sha256`

```json
"sha256": { "statement.md": "…", "in/001.in": "…", "out/001.out": "…" }
```

Lowercase 64-character hex digests over the **exact raw bytes stored in each ZIP member**,
computed *before* test-case newline normalization, so a package can be verified without
interpreting what its members mean.

The map covers every legacy recognized payload member except `problem.json`
itself and `editorial.md`. The problem JSON cannot hash the file containing its
own digest; the editorial uses `editorial.sha256` so older version-2 readers can
ignore the additive member without treating the main manifest as over-complete.

- A **present** map must cover exactly the recognized payload set and match the streamed contents.
  Missing entries, extra entries, and mismatches are all hard errors.
- A redundant `editorial.md` entry is accepted when its digest matches. New
  exports omit it to preserve compatibility with older version-2 readers and
  use `editorial.sha256` as the authoritative declaration.
- An **absent** map imports with an `integrity_manifest_missing` warning, because older packages
  predate the manifest.

### `language_limits`

Keyed by judge language id (`"python3"`, `"cpp20"`, `"rust"`). Stored by Contest only; Arena has
no per-language overrides and writes `{}`.

| Sub-field | Type | Required | Description |
| --- | --- | --- | --- |
| `time_limit_ms` | integer ≥ 1 | **yes** | Time limit override, for **one run** of a test case. The case's budget is this times `repetitions`. (Before version 3 it was that whole budget; see [What version 3 changes](#what-version-3-changes).) |
| `memory_limit_kb` | integer ≥ 1 | **yes** | Memory limit override. |
| `pids_limit` | integer ≥ 1 | **yes** | PIDs limit override. |
| `output_limit_in_bytes` | integer ≥ 1 \| null | no | **May be omitted or null, meaning inherit the problem's limit.** |
| `repetitions` | integer ≥ 1 | no | How many times each test case is run. Absent takes the target language registry's default, which only the importing side knows — which is also why a pre-v3 package's `time_limit_ms` can only be converted after import resolves this. |

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
that decides the verdict. See the
[interactive validator guide](custom-validator/INTERACTIVE_VALIDATOR.md) for
the lifecycle and protocol.

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
`integrity_manifest_missing`, `undeclared_editorial`,
`validator_extension_mismatch`.

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
| `editorial` (text and `editorial.md`) | yes | yes | — | — |
| `editorial.release_policy` | no | **yes** | — | **`release_policy`** (Contest exports `null`) |
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
| `FORMAT_VERSION` | 2 | `shared/services/problem_package/constants.py` |
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
├── editorial.md
├── in/001.in    out/001.out    explanation/001.txt   ← public
├── in/002.in    out/002.out
└── in/003.in    out/003.out
```

`problem.json` (abridged — a real export also carries the `sha256` map):

```json
{
  "format_version": 3,
  "validator_type": "standard",
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
    "python3": {"time_limit_ms": 1000, "memory_limit_kb": 262144, "pids_limit": 64,
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
  "format_version": 3,
  "validator_type": "standard",
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
  "format_version": 3,
  "validator_type": "interactive",
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
