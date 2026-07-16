# Problem Package Format

NOCA problems move between systems as a **plain ZIP archive** (deflate compression). The same
package format is used by both the **Contest (web)** and **Arena** modules, and both modules
consume the same shared parser/validator pipeline so a package built on one side imports cleanly
on the other. Unknown keys are ignored, so a Contest-only package still imports into Arena
(and vice versa).

This document is the canonical reference for the package format. The UI on the import pages is
rendered from `shared/template/_partials/problem_package_format.html`, and the downloadable
sample ZIP is built by `shared/services/sample_problem_package.py`.

Related references:
- [CUSTOM_VALIDATOR.md](CUSTOM_VALIDATOR.md) — interactive-problem lifecycle and verdict protocol
- [SHARED_SERVICES.md](SHARED_SERVICES.md) — `sample_problem_package`, `problem_image`, `tc_zip`
- [../shared/services/sample_problem_package.py](../shared/services/sample_problem_package.py) — the reference "A + B" package
- [../shared/tc_zip.py](../shared/tc_zip.py) — test-case ZIP parser (single source of truth)
- [../shared/services/problem_image.py](../shared/services/problem_image.py) — packaged image loader/validator
- [../shared/services/custom_validator.py](../shared/services/custom_validator.py) — packaged validator lifecycle

## Table of Contents
- [1. Directory layout](#1-directory-layout)
- [2. `problem.json` fields](#2-problemjson-fields)
- [3. `language_limits` object (Contest only)](#3-language_limits-object-contest-only)
- [4. Test cases](#4-test-cases)
- [5. `explanation/` folder](#5-explanation-folder)
- [6. Statement (`statement.md` / `statement.pdf`)](#6-statement-statementmd--statementpdf)
- [7. Illustration image (`image.<ext>`)](#7-illustration-image-imageext)
- [8. Interactive problems (`validator/`)](#8-interactive-problems-validator)
- [9. `interaction/` folder (interactive problems)](#9-interaction-folder-interactive-problems)
- [10. Validation rules](#10-validation-rules)
- [11. Constants and limits at a glance](#11-constants-and-limits-at-a-glance)
- [12. End-to-end examples](#12-end-to-end-examples)

## 1. Directory layout

The package is rooted at the archive root — there is **no enclosing top-level directory**. All
files live at the root or in one of the three reserved subfolders (`in/`, `out/`, `explanation/`),
or — for interactive problems — the optional `validator/` and `interaction/` subfolders.
### Layout produced by Arena exports
```
/
├── problem.json         ← required
├── statement.md         ← required
├── image.<ext>          (optional — png, jpg, jpeg or webp)
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
│   ├── 001.out          (or 001.sol)
│   ├── 002.out          (or 001.sol)
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
├── image.<ext>                     (optional — png, jpg, jpeg or webp)
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
│   ├── 001.out                     (or 001.sol)
│   ├── 002.out                     (or 001.sol)
│   └── ...
└── explanation/                    (optional)
├── 001.txt
└── ...
```
### Flat test-case layout
A flat layout is also accepted on import — `001.in` / `001.out` (or `001.sol`) directly at the
package root, with no `in/` / `out/` directory prefix. This is useful when hand-assembling a
package, but exports always use the directory layout.
## 2. `problem.json` fields
`problem.json` is a single JSON object at the package root. Both importers read it as a plain
mapping; **any key they do not recognize is silently ignored**. This is what makes the format
forwards-compatible: a package built on one side that carries the other side's keys still
imports cleanly.
The example below shows every possible field at once; each field is then described in detail.
```json
{
"title": "A + B",
"author": "John Doe",
"time_limit_ms": 1000,
"memory_limit_kb": 262144,
"pids_limit": 64,
"output_limit_in_bytes": 65536,
"categories": ["sample", "math"],
"notes": "Internal management note.",
"image": "image.png",
"image_caption": "A red square.",
"custom_validator": {
"language_id": "cpp20",
"source_file": "validator/validator.cpp"
},
"source": "ICPC 2025",
"hide_author_show_source": false,
"license": "CC BY-SA 4.0",
"color": "#4287f5",
"language_limits": {
"python3": {
"time_limit_ms": 3000,
"memory_limit_kb": 262144,
"pids_limit": 64,
"output_limit_in_bytes": 1048576,
"repetitions": 3
}
}
}
```
### Common fields (both domains)
| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `title` | string | **yes** | Problem title. Non-empty after trim. Arena caps at 256 characters; Contest at 200. |
| `author` | string | no | Free-text authorship. Arena caps at 80; Contest at 256. On Arena, when omitted the importing user is recorded as both owner and author (`author_is_owner=True`). |
| `time_limit_ms` | integer ≥ 1 | **yes** | Per-test-case time limit in milliseconds. Arena enforces ≥ 100 at the service layer. |
| `memory_limit_kb` | integer ≥ 1 | **yes** | Memory limit enforced via cgroup, in KiB. Arena enforces ≥ 1024. Default 262144 (256 MiB). |
| `pids_limit` | integer ≥ 1 | **yes** | Max processes/threads enforced via cgroup `pids` controller. Default 64. |
| `output_limit_in_bytes` | integer or null | no | Max stdout bytes; `null` = no limit. Arena defaults to 65536 when missing. Empty string is treated as missing. |
| `categories` | array of strings | no | Category names. Arena matches by name/slug and **drops unknowns**; Contest matches by name and **creates unknowns on the fly**. |
| `notes` | string or null | no | Internal management note (not shown to regular users). Arena column is 256 chars; Contest is 512 chars. |
| `image` | filename string or null | no | Path/name of the image file in the package. When set, the file must exist in the archive, otherwise import fails. When omitted, a root-level image file is auto-detected by extension. |
| `image_caption` | string or null | no | Caption shown below the image. |
| `custom_validator` | object | no | Declares an interactive validator. See [section 8](#8-interactive-problems-validator). |
### Arena-only fields (Contest ignores these)
| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `source` | string or null | no | Origin of the problem (e.g. contest name). Max 256 chars. |
| `hide_author_show_source` | boolean | no | If `true`, the public problem page shows `source` instead of author name. Default `false`. |
| `license` | string (≤ 256 chars) or null | no | License shown on the public problem page. |
### Contest-only fields (Arena ignores these)
| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `color` | hex string e.g. `#ff0000` | no | Balloon color assigned to the problem. When absent on import, a random color is picked from `BALLOON_COLORS` (preferring colors not yet used by sibling problems). |
| `language_limits` | object | no | Per-language limit overrides keyed by language id. See [section 3](#3-language_limits-object-contest-only). |
## 3. `language_limits` object (Contest only)
`language_limits` is an object keyed by **language id** (the same id used by the judge, e.g.
`"python3"`, `"cpp20"`, `"rust"`). Each value overrides the problem-level limits for that
language. All keys are optional except where they would have no effect — entries with all three
of `time_limit_ms`, `memory_limit_kb`, `pids_limit` blank are dropped on import.
```json
"language_limits": {
"python3": {
"time_limit_ms": 3000,
"memory_limit_kb": 262144,
"pids_limit": 64,
"output_limit_in_bytes": 1048576,
"repetitions": 3
},
"rust": {
"time_limit_ms": 1000,
"memory_limit_kb": 131072,
"pids_limit": 32,
"output_limit_in_bytes": 1048576,
"repetitions": 1
}
}
```
| Sub-field | Type | Required | Description |
| --- | --- | --- | --- |
| `time_limit_ms` | integer ≥ 1 | When entry is non-empty, required. | Time limit override. |
| `memory_limit_kb` | integer ≥ 1 | When entry is non-empty, required. | Memory limit override. |
| `pids_limit` | integer ≥ 1 | When entry is non-empty, required. | PIDs limit override. |
| `output_limit_in_bytes` | integer or null | Optional. | Output size override. |
| `repetitions` | integer | Optional. Default: 1 (fallback) or the language's `profiling_repetitions_default`. | Number of profiling repetitions for this problem/language pair. |
On import, per-language `language_limits` entries whose `language_id` is not enabled in the
target contest are **silently dropped**. The dropped ids are returned in the import result as
`skipped_language_ids`.
A legacy top-level `repetitions` field is accepted and ignored.
## 4. Test cases
Test cases are stored as paired files under `in/` and `out/`. The shared parser
(`shared/tc_zip.py`) is the single source of truth.
### File naming
The following regexes are matched case-insensitively against archive member names:
- Directory layout: `^(in|out)/0*([1-9]\d{0,2})(\.in|\.out|\.sol)?$`
- Flat layout: `^0*([1-9]\d{0,2})\.(in|out|sol)$`
Rules:
- Leading zeros in filenames are stripped, so `001.in` and `1.in` refer to the same ordinal.
- `.out` and `.sol` are interchangeable for outputs; mixing them across pairs is allowed.
- Maximum ordinal is 999 (3 digits).
- Maximum **1000 test cases** per problem (`MAX_TESTCASES = 1000`).
### Pairing and ordering
- Inputs and outputs are paired by ordinal.
- All `in/` ordinals must have a matching `out/` ordinal, and vice-versa — **unless** the
problem carries a `custom_validator` (input-only interactive problems).
- Ordinals are **remapped contiguously starting at 1** in the result, regardless of gaps in
source filenames.
Example pairing:
```
in/001.in   out/001.out
in/002.in   out/002.out
in/007.in   out/007.sol       ← .sol is accepted
in/010.in   out/010.out
↑ ordinals remap to 1, 2, 3, 4 on import
```
### Content rules
- All test-case content must be valid **UTF-8**. Binary test cases are rejected (Arena enforces
this strictly; Web does so via `parse_testcases_zip`).
- CRLF and lone CR are normalized to LF on import (`\r\n` → `\n`, `\r` → `\n`).
- Empty content and empty lines are preserved.
- There is no per-file size cap on import (the 10 KiB cap is a textarea editing limit only —
large test cases can still be uploaded as files).
### On-disk layout after import
In both domains, test cases are written to:
```
<NOCA_PROBLEM_TESTCASE_DIR>/<contest|arena>/<problem_id>/NNN.in
<NOCA_PROBLEM_TESTCASE_DIR>/<contest|arena>/<problem_id>/NNN.out
```
where `NNN` is `ordinal:03d` (zero-padded, 1-based).
### Public vs. secret
There is no separate metadata file for the public/secret flag. After import:
- All packaged test cases are imported as **secret** (`is_sample=False`) on both domains.
- When the package declares a `custom_validator`, all packaged test cases are imported as
**public samples** instead — interactive judging never reads the test-case files, so there
are no secrets to protect.
## 5. `explanation/` folder
Author notes that explain why a test case has its expected output. They are shown to contestants
**below the sample** in the problem-statement view.
```
explanation/
├── 001.txt    ← optional UTF-8 text for test case #1
├── 002.txt    ← optional
└── ...
```
### File naming
- Regex: `^explanation/0*([1-9]\d{0,2})\.txt$` (case-insensitive).
- Matched by ordinal with the test cases; remapped to the new ordinal space.
- An explanation for an ordinal with no matching test case is silently **ignored** (no
validation or decode error).
### Content rules
- UTF-8 only; invalid UTF-8 raises `ValueError("Explanation for ordinal N is not valid UTF-8.")`.
- No size limit (the previous 1024-char cap was removed).
- Whitespace-only explanations are dropped to `null`.
Example (`explanation/001.txt`):
```
The two numbers on the input line are added together.
```
## 6. Statement (`statement.md` / `statement.pdf`)
The problem statement must be packaged as one of:
- `statement.md` — Markdown (UTF-8). Required on Arena. Optional on Contest when a `statement.pdf` is present.
- `statement.pdf` — PDF. Contest only. When present, takes precedence over `statement.md`.
### Markdown validation
`statement.md` is validated through `validate_md_content` (in `shared/problem_statement_markdown.py`):
- Must be valid UTF-8.
- Must not exceed **512 KiB**.
- Must not contain:
- `link` — any Markdown link, autolink, reference link, or bare URL (`https://...`, etc.).
- `image` — any `![alt](src)` Markdown image.
- `html` — HTML blocks and inline HTML.
- Allowed authoring features include **LaTeX** (`$...$`) and **mermaid** fenced code blocks.
Validation errors raise a single `ValueError` listing all detected disallowed features.
### Example statement (`statement.md`)
```markdown
# A + B
Read two integers and print their sum.
## Input
A single line with two integers, `a` and `b`, separated by a space.
## Output
A single line with the value of `a + b`.
## Example
Input:
```
1 2
```
Output:
```
3
```
## Note
The sum of two integers fits in a 64-bit signed type. Hint: think about the
formula $a + b$ and the time complexity $O(1)$.
```
## 7. Illustration image (`image.<ext>`)
An optional illustration shown below the statement.
### Supported formats
| Extension | MIME type |
| --- | --- |
| `png` | `image/png` |
| `jpg` / `jpeg` | `image/jpeg` |
| `webp` | `image/webp` |
The MIME map is the single source of truth: `shared/services/problem_image.py` defines
`MIME_TO_EXT` and `EXT_TO_MIME`.
### Rules
- Maximum size: **2 MiB** (`MAX_PROBLEM_IMAGE_BYTES = 2 * 1024 * 1024`).
- The file is validated through `ImageProcessingService.process_base64` (resizes/strips metadata).
- When `problem.json.image` names a specific file, that file **must exist** in the archive —
otherwise import fails with `"problem.json references image '...' which is not present in the ZIP."`.
- When `problem.json.image` is absent, the loader auto-detects the first root-level file whose
extension matches `EXT_TO_MIME`.
- `image_caption` (string or null) is shown below the image. Whitespace-only captions are dropped.
- The image is part of the statement a contestant reads and is therefore **included in public
exports** too (not just full exports).
## 8. Interactive problems (`validator/`)
A custom validator turns a problem into an **interactive** problem: the contestant's program
talks to a validator program that decides the verdict. See [CUSTOM_VALIDATOR.md](CUSTOM_VALIDATOR.md)
for the full lifecycle and protocol.
### Package layout
```
validator/
└── validator.py     ← UTF-8 validator source (required when custom_validator is declared)
in/
├── 001.in           ← test cases: inputs only, no out/ directory
└── 002.in
```
Exports name the validator source `validator/validator<ext>`, where `<ext>` is the source
extension for the validator's language (e.g. `validator.py`, `validator.cpp`). The exact name is
declared in `problem.json` (see below); the older locked name `validator/source.txt` is still
accepted on import.
An interactive problem's test cases carry **input only**. Each case's input is fed to the
validator's standard input before it starts talking to the contestant, so it parametrizes one
round of the conversation rather than declaring an expected answer. The package therefore ships
`in/NNN.in` files and **no** `out/NNN.out`; any output files present are ignored on import.

Every test case of an interactive problem is **secret**. There is nothing meaningful to publish:
a bare input reveals a secret without showing what the contestant is supposed to do with it. The
public examples come from the [`interaction/` folder](#9-interaction-folder-interactive-problems)
instead. Imports therefore mark every case secret, and the application refuses to make a case
public while a validator is configured. At least one secret test case is required before the
problem can be enabled or accept submissions.

### Declaring a validator in `problem.json`
```json
{
"custom_validator": {
"language_id": "cpp20",
"source_file": "validator/validator.cpp"
}
}
```
| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `custom_validator.language_id` | string | **yes** | Must be an active judge language id (e.g. `cpp20`, `python3`). |
| `custom_validator.source_file` | string | **yes** | A single file directly inside `validator/` (e.g. `"validator/validator.cpp"`); no nested paths or `..` traversal. Exports write `validator/validator<ext>`. |
### Source rules
- UTF-8, non-empty, non-blank.
- Maximum **256 KiB** (`MAX_CUSTOM_VALIDATOR_SOURCE_BYTES = 256 * 1024`).
- Validation raises `ValidatorUploadError` on:
- Empty or blank source.
- Oversized source.
- Invalid UTF-8.
- Missing `language_id`.
- Unsafe `source_file` path (not a single file directly inside `validator/`).
- The declared `source_file` member missing from the archive.
### Behaviour on import
- The validator is staged as a `PENDING` candidate via `stage_candidate` (in
`shared/services/custom_validator.py`), and a compile job is enqueued on the
profiling-priority queue after the transaction commits.
- The problem **cannot accept submissions** until the candidate compiles successfully and is
promoted to the active revision.
- Full exports of an interactive problem include the validator source; **public exports never
include it**.
## 9. `interaction/` folder (interactive problems)
An interactive problem shows contestants **sample interactions** — worked examples of the
conversation their program will have with the validator — instead of sample test cases. Each one
is a transcript, optionally paired with an explanation.

```
interaction/
├── 001.interaction   ← required: the transcript, as a raw JSON string
├── 001.explain       ← optional: plain-text explanation of this conversation
├── 002.interaction
└── ...
```

`NNN` is a zero-padded, 1-based integer that reflects the exact order the author arranged the
interactions in. Gaps are closed on import (the same contiguous remap test cases get), so a
hand-assembled `007` / `042` pair imports as `1` / `2`.

### `NNN.interaction` — the transcript
The file holds one JSON object, in the same shape the judge records for a real interactive
attempt (`submission_interactive_attempts.transcript`), so one renderer serves both:

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
| `lines[].dir` | string | **yes** | `"validator"` (validator → contestant) or `"user"` (contestant → validator). |
| `lines[].line` | string | **yes** | The protocol line, without its trailing newline. |
| `truncated` | bool | no | Defaults to `false`. |

In the Arena and Contest admin UIs the same transcript is authored as plain text, one message per
line, using the exact two-character prefixes `> ` (validator speaking) and `< ` (contestant
speaking):

```
> 3
< 5
> !8
```

The parser is strict: every line must carry one of those prefixes **including the space**. Bare
`>` / `<`, leading whitespace, and blank lines are rejected. An empty protocol line is written as
`> ` with nothing after it.

### `NNN.explain` — the explanation
Optional, plain UTF-8 text, rendered as Markdown below the conversation on the problem page. An
`.explain` file with no matching `.interaction` file is ignored.

### Rules
- **At most 5** sample interactions per problem (`MAX_SAMPLE_INTERACTIONS`).
- **Only meaningful with a validator.** If the package declares no `custom_validator`, the whole
  `interaction/` folder is **silently dropped** on import.
- A package that **does** declare a validator but ships no interactions imports successfully; the
  importer warns that the problem has no examples to show contestants.
- Exports include only the interactions that are currently visible. Interactions hidden by a
  "keep" choice when a validator was removed stay out of the package, and the survivors are
  renumbered so the export has no gaps.
- Because sample interactions are the public examples of an interactive problem, they ship in
  **public exports** too — unlike the validator source, which never does.

## 10. Validation rules
The following rules are enforced at import time, by the relevant shared helper. Each importer
raises `ValueError` (or `ValidatorUploadError` for validator-specific problems) on failure.
### General
- The archive must be a valid ZIP file.
- `problem.json` must be present, valid UTF-8, parse as JSON, and contain a JSON object at the top
level.
- The top-level values for `title`, `time_limit_ms`, `memory_limit_kb`, `pids_limit` must be
present and valid:
- `title` — non-empty after trim.
- `time_limit_ms`, `memory_limit_kb`, `pids_limit` — integer ≥ 1. Strings such as `"1500"`
are accepted (cast through `int(str(value))`).
- `output_limit_in_bytes`, when present and non-null, must be an integer ≥ 1; missing or
empty string is treated as null.
### Statement
- Arena: `statement.md` is required.
- Contest: either `statement.pdf` or `statement.md` is required.
- Markdown must pass `validate_md_content` (size cap, allowed features only — see
[section 6](#6-statement-statementmd--statementpdf)).
### Test cases
- At least one test case is required. Every problem needs one, interactive or not.
- Every case of an interactive problem is imported as **secret**; an interactive problem has no
  public test cases (see [section 9](#9-interaction-folder-interactive-problems)).
- Maximum 1000 test cases.
- UTF-8 only.
- Inputs without matching outputs (and vice-versa) raise `ValueError` with the offending ordinals.
  This pairing rule does **not** apply to a validator package: its cases are input-only, and any
  output files in the archive are ignored.
- Invalid UTF-8 raises `ValueError` listing the offending ordinal and stream
(`input` or `output`).
- CRLF and lone CR are normalized to LF on import.
### Explanations
- UTF-8 only.
- Orphan explanations (no matching input file) are silently dropped.
### Image
- `problem.json.image`, when set, must reference a file present in the archive.
- The image must pass `ImageProcessingService.process_base64`.
- Maximum 2 MiB.
### Custom validator
- `custom_validator` must be a JSON object.
- `language_id` must be a non-empty string.
- `source_file` must be a single file directly inside `validator/` (e.g. `"validator/validator.py"`); no nested paths or `..` traversal.
- That member must exist in the archive.
- Source must be valid UTF-8, non-empty, non-blank, ≤ 256 KiB.
### Sample interactions
- Dropped entirely when the package declares no `custom_validator`.
- Maximum 5 per problem.
- Each `interaction/NNN.interaction` must be valid UTF-8 and parse as a JSON object holding a
  non-empty `lines` array, where each entry has `dir` in (`"user"`, `"validator"`) and a string
  `line`. `truncated`, when present, must be a boolean.
- Orphan `.explain` files (no matching `.interaction`) are silently dropped.
- Ordinals are remapped contiguously from 1.
### Per-language limits (Contest only)
- `language_limits` must be a JSON object.
- Each entry must include at least one of `time_limit_ms`, `memory_limit_kb`, `pids_limit`
(all blank ⇒ skipped).
- Languages not enabled in the target contest are dropped and reported in
`skipped_language_ids`.
### Categories
- Arena: matched by name (case-insensitive) or slug; unknown categories are dropped.
- Contest: matched by name; unknown categories are created on the fly.
## 11. Constants and limits at a glance
| Constant | Value | Defined in |
| --- | --- | --- |
| `MAX_TESTCASES` | 1000 | `shared/tc_zip.py` |
| `MAX_INLINE_TESTCASE_BYTES` | 10 KiB | `shared/tc_zip.py` (textarea editing gate only) |
| `MAX_PROBLEM_IMAGE_BYTES` | 2 MiB | `shared/services/problem_image.py` |
| `MAX_CUSTOM_VALIDATOR_SOURCE_BYTES` | 256 KiB | `shared/services/custom_validator.py` |
| `MAX_CUSTOM_VALIDATOR_COMPILE_LOG_CHARS` | 16 384 | `shared/services/custom_validator.py` |
| `MAX_SAMPLE_INTERACTIONS` | 5 | `shared/services/sample_interactions.py` |
| Statement Markdown cap | 512 KiB | `shared/problem_statement_markdown.py` |
| Arena `output_limit_in_bytes` default | 65 536 | `arena/services/admin_problem_io_service.py` |
## 12. End-to-end examples
### 11.1 Minimal Arena package (the "A + B" sample)
```
noca-sample-problem-a-plus-b.zip
├── problem.json
├── statement.md
├── in/001.in
├── out/001.out
├── in/002.in
├── out/002.out
├── in/003.in
├── out/003.out
└── explanation/001.txt
```
`problem.json`:
```json
{
"title": "A + B",
"author": "John Doe",
"notes": "Sample problem",
"license": "cc sa-by",
"categories": ["sample", "math"],
"time_limit_ms": 1000,
"memory_limit_kb": 262144,
"pids_limit": 64,
"output_limit_in_bytes": 1048576,
"color": "#4287f5",
"language_limits": {
"python3": {
"time_limit_ms": 3000,
"memory_limit_kb": 262144,
"pids_limit": 64,
"output_limit_in_bytes": 1048576,
"repetitions": 3
},
"rust": {
"time_limit_ms": 1000,
"memory_limit_kb": 131072,
"pids_limit": 32,
"output_limit_in_bytes": 1048576,
"repetitions": 1
}
}
}
```
`statement.md`:
```markdown
# A + B
Read two integers and print their sum.
## Input
A single line with two integers, `a` and `b`, separated by a space.
## Output
A single line with the value of `a + b`.
```
`in/001.in`:
```
1 2
```
`out/001.out`:
```
3
```
`in/002.in`:
```
2 5
```
`out/002.out`:
```
7
```
`in/003.in`:
```
10 20
```
`out/003.out`:
```
30
```
`explanation/001.txt`:
```
The two numbers on the input line are added together.
```
This is the exact package built by `build_sample_problem_package()` in
`shared/services/sample_problem_package.py` and served by the import pages.
### 11.2 Contest package with PDF statement and an image
```
my-contest-problem.zip
├── problem.json
├── statement.pdf
├── image.png
├── in/001.in
└── out/001.out
```
`problem.json`:
```json
{
"title": "Geometry Maze",
"author": "Jane Doe",
"notes": "Created for the 2025 regionals.",
"time_limit_ms": 2000,
"memory_limit_kb": 262144,
"pids_limit": 64,
"output_limit_in_bytes": 1048576,
"categories": ["geometry", "graph"],
"image": "image.png",
"image_caption": "Figure 1. The maze layout.",
"language_limits": {
"cpp20": {
"time_limit_ms": 2000,
"memory_limit_kb": 262144,
"pids_limit": 64,
"output_limit_in_bytes": 1048576,
"repetitions": 1
}
}
}
```
`image.png` is a 2 MiB max PNG illustrating the maze.
### 11.3 Interactive Arena problem (custom validator)
```
number-guessing.zip
├── problem.json
├── statement.md
├── validator/validator.py
├── in/001.in       ← the round the validator plays; no out/ directory
└── in/002.in
```
`problem.json`:
```json
{
"title": "Number Guessing",
"author": "Jane Doe",
"source": "ICPC 2025",
"hide_author_show_source": false,
"license": "CC BY-SA 4.0",
"time_limit_ms": 2000,
"memory_limit_kb": 262144,
"pids_limit": 64,
"output_limit_in_bytes": null,
"categories": ["interactive", "search"],
"notes": "Adapted from ICPC regionals.",
"custom_validator": {
"language_id": "python3",
"source_file": "validator/validator.py"
}
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
`in/001.in` — an easy round (secret 42, 50 queries):
```
42 50
```
`in/002.in` — a harder round the contestant must also pass:
```
999999 20
```
The judge runs the validator once per case, in order, feeding it that case's line before the
contestant says anything. The submission is Accepted only if both rounds end with exit `0`.
### 11.4 Flat-layout package
A flat layout is also accepted on import (though exports always use the directory layout):
```
flat-problem.zip
├── problem.json
├── statement.md
├── 001.in
├── 001.out
├── 002.in
└── 002.sol       ← .sol accepted as .out
```
This parses identically to the directory-layout version with `in/001.in`, `out/001.out`, etc.
