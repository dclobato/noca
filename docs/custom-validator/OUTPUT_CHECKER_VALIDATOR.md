# Output checker validator rationale

NOCA needs a native custom output-checker strategy for problems where many
different outputs can be correct. An output checker runs after the contestant
program finishes and validates the complete output against the problem rules.
It complements the existing built-in comparison and interactive validation
strategies without changing either one.

This document records the initial rationale and architectural direction. It is
not an implementation specification, and the feature described here is not yet
available.

## Decision

NOCA will model output checking as a third explicit validation strategy:

- `STANDARD` runs the contestant and compares its output with the expected
  output through NOCA's built-in comparator.
- `OUTPUT_CHECKER` runs the contestant normally, then passes the completed
  output to a problem-specific checker.
- `INTERACTIVE` runs the contestant and validator concurrently and connects
  their input and output streams.

The strategy must be stored explicitly. In a versioned problem package, the
required `validator_type` field carries `standard`, `interactive`, or `checker`.
NOCA must not infer the strategy from validator source, checker semantics,
sample interactions, or the presence of expected output. Existing problems
without an interactive validator map to `STANDARD`, and existing
custom-validator problems map to `INTERACTIVE`.

As of v16.0.0, NOCA has support for problems to be judged using STANDARD and
INTERACTIVE validators.

## Why output checking is a separate strategy

The built-in comparator works when a test case has one expected output, with
only NOCA's supported whitespace and presentation differences. It cannot
evaluate a schedule, assignment, graph, proof, or other constructed result
when many arrangements satisfy the same constraints.

Interactive validation solves a different problem. It starts the contestant
and validator together and lets them exchange messages while both processes
are running.

An output checker does not communicate with a live contestant. It receives
the contestant's completed output and decides whether that output is a valid
solution for one test case.

Keeping these strategies separate preserves clear execution and failure
boundaries:

- Contestant execution limits remain the same for `STANDARD` and
  `OUTPUT_CHECKER`.
- Checker time, memory, output, and failures belong to trusted judging code,
  not to the contestant.
- A checker crash is an internal judging failure, not `WA` or contestant `RE`.
- Interactive protocol behavior and transcript storage remain exclusive to
  `INTERACTIVE`.

## Native NOCA contract

The feature and its contract belong entirely to NOCA. The design must use NOCA
concepts and remain independent of external judge formats, helper libraries,
invocation conventions, and verdict protocols.

NOCA must define its own stable checker contract. That contract must give the
checker access to the current test-case input, the contestant's complete
bounded output, and optional problem-owned reference data when configured. It
must also define how the checker reports acceptance, rejection, diagnostics,
and an internal failure.

NOCA uses the same checker input transport in Web and Arena. The transport is
defined below entirely in terms of NOCA-owned files, environment variables, and
runtime boundaries. The checker verdict protocol is fixed separately below.

### Checker input files

Before every checker attempt, NOCA creates a clean `/data` directory inside the
checker container and stages these standard files:

- `/data/testcase.in` always exists and contains the current test-case input;
- `/data/contestant.out` always exists and contains the contestant's complete
  bounded output from the last configured repetition;
- `/data/reference.out` exists only when the current test case has reference
  output.

The three files contain the exact stored or captured bytes. NOCA performs no
text decoding, newline conversion, tokenization, or other transformation while
staging them. A zero-byte file is valid. In particular, a present zero-byte
`/data/reference.out` means that the case has an explicitly empty reference
output, while an absent `/data/reference.out` means that the case has no
reference output.

The checker receives no file-path arguments because these absolute paths are
fixed. NOCA invokes it through the selected language's registered run command.
The checker may read or modify its private staged copies and may create
temporary files under `/data` or `/tmp`. Both locations are ephemeral. NOCA
must reset them before every case attempt, including a retry, and destroys all
checker-created files when the judgment container is destroyed. A checker must
not rely on a temporary file surviving another case, retry, judgment, or
container.

## Execution boundary

The Autojudge currently executes a contestant and then applies the built-in
output comparison in the same runner operation. Supporting output checkers
cleanly requires separating these responsibilities:

1. Compile the contestant and stop with `CE` when compilation fails.
2. Execute the contestant and produce a bounded execution result.
3. Stop with `TLE`, `MLE`, `OLE`, or `RE` when contestant execution produces
   that verdict.
4. For a normal contestant exit with code `0`, select the configured validation
   strategy.
5. Apply the built-in comparator or run the custom output checker.
6. Store the contestant result and checker diagnostics under their respective
   identities.

This boundary also creates a natural extension point for future validation
strategies without copying the contestant-execution pipeline.

## Repetitions, profiling, and related workflows

`OUTPUT_CHECKER` must reuse the existing standard contestant-execution,
repetition, profiling, and Auto-Limit workflow. It replaces the built-in output
comparison; it does not run after that comparison. This distinction prevents
NOCA from rejecting an alternative valid output before the custom checker can
evaluate it.

For each test case, NOCA must:

1. Run every configured contestant repetition under the existing shared
   execution budget.
2. Stop immediately when any repetition produces `TLE`, `MLE`, `OLE`, `RE`, or
   another contestant-execution failure. The checker does not run in this case.
3. Aggregate contestant resource measurements using the current rules,
   including total wall time and peak memory, output, and process counts across
   all repetitions.
4. Retain the complete bounded stdout produced by the last successful
   repetition.
5. After every repetition succeeds, invoke the output checker exactly once for
   that test case, using only the last repetition's output for correctness.
6. Use the checker's result as the test-case correctness verdict.

Consequently, semantically incorrect output from an earlier successful
repetition does not affect correctness. An execution or resource-limit failure
from any repetition still ends the test case. Peak output usage is measured
across all repetitions even though only the contents of the last output are
checked. With one repetition, this contract reduces naturally to checking that
single output. NOCA must never apply its built-in output comparator to an
`OUTPUT_CHECKER` test case.

NOCA must also inherit the standard test-case traversal and final-verdict
workflow. It executes test cases in ordinal order and persists a result for
every case that actually runs. An `AC` checker result advances to the next case.
The first `WA`, `PE`, or contestant-execution failure stops traversal and becomes
the judgment verdict. An internal checker failure aborts the judgment as
`FAILED` under the failure rules below. Cases after the stopping case are not
executed and receive no result row. The judgment is `AC` only when every test
case executes and returns `AC`.

Profiling and Auto-Limit retain their current hard execution limits, configured
repetition counts, shared time budget, and resource aggregation. The output
checker runs once per profiling test case, after the final repetition, and only
an `AC` checker result makes that profiling test case correct. A checker result
of `WA` or `PE` rejects the profiling solution for that case. A checker contract
error, crash, timeout, or resource failure is an internal profiling failure,
not a contestant verdict.

Checker execution time, memory, output, and process usage are excluded from
contestant profiling and Auto-Limit calculations. NOCA may publish calculated
limits only after every profiling test case completes with an `AC` checker
result.

The same contract applies to Web and Arena submissions, solution tests,
profiling and Auto-Limit runs, rejudging, and recovered jobs. These entry points
must not develop separate repetition or correctness behavior.

## Runtime limits and retry policy

An output checker is trusted problem-author code and must use the same runtime
safety policy as an interactive validator. NOCA must reuse the interactive
validator settings and container-pool behavior rather than introduce a second
set of checker-specific deployment settings.

The contestant's problem time, memory, and PID limits do not apply to the
checker. The checker runs under isolate in its own network-disabled run
container, with the shared Docker container memory and PID limits as outer
backstops. The current defaults are 512 MB through
`NOCA_JUDGE_CONTAINER_MEM_LIMIT_MB` and 256 PIDs through
`NOCA_JUDGE_CONTAINER_PID_LIMIT`. Checker stdout and stderr remain subject to
the diagnostic bounds defined by this contract.

Each checker attempt uses the same emergency wall-clock watchdog as an
interactive-validator attempt:
`NOCA_JUDGE_CUSTOM_VALIDATOR_WATCHDOG_SECONDS`, currently 300 seconds by
default. The watchdog applies separately to each test-case attempt. It is a
judge safety limit, not a contestant time limit, and its expiration is an
internal validator failure rather than contestant `TLE`.

A signal, process startup or communication failure after successful input
staging, watchdog expiration, or checker container resource failure is a
retryable checker crash. NOCA must retry the checker once for the same test case,
using the already captured last-repetition output. The contestant is not
compiled or executed again. The retry must use a fresh checker container because
the failed container is not safe to reuse.

If the retry produces a valid checker exit, judging continues from that result.
If the second attempt also crashes, NOCA must abort the judgment as an internal
failure and apply the same crash-containment operation used for an interactive
validator in that problem domain:

- **Contest:** mark only the current judgment `FAILED` with staff-visible
  diagnostics. Keep the active checker revision `VALID`, keep the problem
  available, and do not remove other queued judgments. A checker crash never
  disables a Contest problem, regardless of the contest phase.
- **Arena:** mark the active checker revision `RUNTIME_FAILED`, disable the
  problem, fail and dequeue its other queued judgments, and notify the problem
  owner. A validated replacement revision is required before the problem can be
  enabled and dispatched again.

### Input-staging failures

An input-staging failure happens before the checker starts and belongs to NOCA,
not to the contestant or checker revision. An intentionally absent
`/data/reference.out` is not a failure. A configured reference file that cannot
be loaded is a failure, as is an unavailable test-case input or captured
contestant output.

Missing or inconsistent authoritative problem data is not container-retried
because a fresh container cannot restore it. NOCA immediately marks the current
judgment, solution test, or profiling run `FAILED`. For a transient container
staging failure, such as an error creating `/data` or copying a file, NOCA
retries staging once in a fresh checker container with the already captured
contestant output. It must not compile or execute the contestant again. If the
second staging attempt fails, the current job becomes `FAILED`.

NOCA persists the executed case attempt and preserves contestant execution
measurements already collected, but records internal failure ownership rather
than a contestant correctness verdict. Contestants receive only a generic
internal-judging-failure message. Detailed storage, permission, and container
diagnostics are restricted to judges and operators.

Input-staging failure never marks the checker revision `RUNTIME_FAILED`, disables
the problem, or removes other queued judgments in either Contest or Arena. Those
containment actions apply only after the checker starts and satisfies the
checker-crash conditions above.

Clean checker exits are not crashes and are not retried. Exit codes `0`, `1`,
and `2` produce their documented contestant verdicts. Exit code `3` and any
other undocumented clean exit produce an internal validator failure according
to the checker verdict protocol, without a crash retry.

NOCA must also reuse the interactive validator's container lifecycle:

- Acquire one checker run container for a judgment and copy the compiled
  checker artifact into it once.
- Start a fresh checker process and reset per-run artifacts for each test case.
- Reuse that container for the next test case only after a clean `AC` checker
  exit.
- Discard a crashed container before the single retry.
- Destroy the checker container when the judgment ends; never return an
  executed container to the warm pool or reuse it for another judgment.

If a checker crash is retried successfully, the fresh retry container may be
reused for later test cases under these same rules. Container reuse never
changes the rule that the checker receives only the last contestant repetition
for the current test case.

## Checker verdict protocol

The contestant and checker have separate failure ownership. The checker runs
only after the contestant compiles successfully and every configured repetition
exits normally with code `0` while staying within every contestant resource
limit. Otherwise, NOCA returns the contestant's `CE`, `TLE`, `MLE`, `OLE`, or
`RE` verdict without starting the checker.

When the checker runs, its exit code is the authoritative verdict:

| Exit code | Verdict |
| --- | --- |
| `0` | `AC` |
| `1` | `WA` |
| `2` | `PE` |
| `3` | Internal validator failure: checker contract error |

Exit code `3` is the checker's intentional abort for a missing required input,
invalid environment value, or another checker-contract error. Any other clean
exit code or any signal is also an internal validator failure, not a contestant
verdict. Checker process startup failure after successful input staging,
timeout, memory exhaustion, or output-limit violation is an internal validator
failure. These failures must never become contestant `RE`, `WA`, or another
contestant verdict.

`PE` means that the semantic result is correct, but its presentation violates
an enabled case or whitespace rule. The checker must apply these outcomes:

- A case difference under case-insensitive comparison is `AC`.
- A case difference under case-sensitive comparison, with everything else
  correct, is `PE`.
- A whitespace difference under whitespace-insensitive comparison is `AC`.
- A whitespace difference under whitespace-sensitive comparison, with
  everything else correct, is `PE`.
- A floating-point difference outside the configured tolerances is `WA`.

The checker may also return `PE` for another purely presentational violation
defined by the problem. Malformed, incomplete, semantically incorrect, or
numerically incorrect output is `WA`.

For checker exits `0`, `1`, and `2`, NOCA presents the checker's bounded stdout
to the contestant as **Validation result** and its bounded stderr as
**Validation errors**. Either stream may be empty. The exit code determines the
verdict; stream content cannot override it. For example, an exit code of `1`
may carry `Wrong Answer` on stdout and `Too many rounds` on stderr.

When the checker has an internal validator failure, NOCA retains its stdout and
stderr for judges and operators only. Fault diagnostics may contain stack
traces, internal paths, or secret test information and must not be exposed to
the contestant.

## Problem and test-case model

An output-checker problem needs at least one test case and an active, validated
checker before it can accept submissions. Unlike an interactive problem, it
continues to use normal sample and secret test cases rather than sample
interaction transcripts.

The checker always needs the test-case input and contestant output. Some
checkers also need a trusted reference answer, optimum value, tolerance, or
other problem-owned data, while constraint-only checkers may not need any
reference data. Each checker test case may independently have one optional
reference-output file. A problem may therefore mix cases with and without
reference output. Reference-data presence must not become the
validation-strategy discriminator.

### Reference-output contract

The reference output is opaque UTF-8 text owned by the problem. Opaque means
that NOCA assigns no meaning to its contents: it may hold an expected answer, a
known optimum, serialized constraints, or any other text that makes sense to
the checker. Binary data and invalid UTF-8 are forbidden. NOCA must validate the
encoding and normalize CRLF (`\r\n`) and lone CR (`\r`) line endings to LF
(`\n`). It must not otherwise interpret, tokenize, compare, trim, normalize
Unicode, change whitespace, or add or remove a final newline. NOCA stores the
normalized UTF-8 bytes as the canonical reference output. Download, export,
backup, restore, and checker staging must preserve those canonical bytes.

The initial version supports exactly one optional reference-output file per
test case. In problem packages and test-case archives, that file is the case's
`.out` member. At runtime, NOCA stages it as `/data/reference.out`. Arbitrary
auxiliary files and multiple named reference files are outside the initial
contract.

A present zero-byte `.out` is an explicitly empty reference file. An absent
`.out` means that the case has no reference data. An `.out` member without the
corresponding `.in` member is invalid. Package import, archive upload, and
authoring operations must preserve the distinction between absent and empty
files, and must allow different cases in one checker problem to make different
choices.

NOCA stores the optional file beside the test-case input and uses nullable
output-size metadata to record presence: `null` means absent, and `0` means a
present empty file. It calculates the size and every package or backup digest
after newline normalization. Administrative test-case workflows must let an
authorized problem owner upload, replace, delete, and download the opaque text
file. The source of truth is the file operation, not a text field. An interface
must show a bounded, read-only text preview and keep the complete file available
through download.

Full problem-package exports, test-case archive downloads, backups, and restores
must include every present `.out` and preserve its canonical normalized bytes.
Public exports must include a present `.out` only for sample cases. Secret `.in`
and `.out` files must never appear in a public package or contestant-facing
download.

### Public sample reference data

A sample checker's reference output is public problem data. When a sample case
has an `.out`, the problem page must present it together with the corresponding
`.in`. Both files must have adjacent download actions. NOCA must serve the
opaque `.out` as an attachment with `text/plain; charset=utf-8` and must also
show the bounded preview defined above. An absent sample `.out` produces no
reference-output preview or download and is not represented as an empty file.

### Missing reference data

The absence of `/data/reference.out` is a valid test-case configuration and is
not an input-staging failure. NOCA cannot determine from source code whether a
checker algorithm requires reference data, and candidate validation does not
execute the checker. A checker that requires the file must detect its absence
and exit with code `3`. NOCA then reports the documented internal checker
contract failure, without a crash retry, without assigning a contestant
verdict, and without changing the active checker revision or the problem's
enabled state.

### Reference-data changes and rejudging

Adding or removing a test case, replacing its input, or adding, replacing, or
removing its reference output is a test-case change. It does not alter completed
judgments, invalidate the active checker, disable the problem, or automatically
requeue submissions. An administrator or problem owner who wants all existing
submissions evaluated against the changed test cases must explicitly request a
full rejudgment for that problem. The rejudgment uses the active checker
configuration and test-case data available at its dispatch time.

A queued judgment that has not been dispatched uses the latest published test
cases when it is dispatched. At dispatch, Autojudge must load one coherent,
job-local copy of the complete ordered test-case set, including each optional
reference output. A running judgment continues to use that copy, so changing a
test case cannot make one judgment mix old and new files. The copy is ephemeral
and is destroyed when the job ends; it does not create historical test-case
revision retention.

Expected output remains mandatory for `STANDARD`. Interactive test cases remain
input-only. The output-checker policy can therefore evolve independently after
the strategy becomes explicit.

### Comparison policy

An output checker may need to compare contestant tokens with trusted values
while still applying problem-specific presentation and numeric rules. NOCA must
store this comparison policy with the active checker configuration and load it
together with the active source and language at dispatch. The Autojudge passes
that loaded policy to the checker on every invocation in the job. Making the
policy explicit keeps checker behavior reusable and consistent between Web,
Arena, solution tests, and rejudging.

The comparison policy consists of:

- whether text comparison is case-sensitive;
- whether differences in whitespace amount are accepted or rejected;
- an optional relative floating-point tolerance;
- an optional absolute floating-point tolerance.

The two Boolean settings must always have explicit values in a package and in
the checker process environment. The authoring UI may initialize both to
`false`, but that initialization becomes an explicit authored value rather than
a package fallback. Every export writes both values. Missing Boolean metadata
is invalid.

#### Comparison authority

Comparison-policy responsibility is divided across the authoring applications,
the Autojudge, and the checker:

- Web and Arena authoring and package-import services must validate all four
  authored settings before they stage a checker candidate.
- The Autojudge must defensively validate the persisted settings, translate
  them into the fixed environment variables below, and inject them for every
  checker invocation. Invalid persisted settings are an internal judging
  failure, and the checker must not start.
- The checker is solely responsible for applying the settings and returning
  `AC`, `WA`, or `PE` through the checker verdict protocol.
- The Autojudge must trust the checker's verdict and must not perform a second
  built-in or custom comparison afterward.

The tokenization, whitespace, text, and floating-point rules below are a
normative checker-author contract whenever a checker compares a contestant token
with its corresponding answer-file token. They are not optional recommendations.
A constraint-only checker, or a checker operation that does not compare those
tokens, may ignore settings that do not apply to that operation.

NOCA cannot prove semantic compliance during candidate validation because that
workflow compiles the checker without executing it. A checker that silently
violates these rules is defective even if it compiles and returns a documented
exit code. NOCA cannot detect that defect automatically; after the author fixes
and promotes the checker, affected submissions require explicit rejudging.

#### Tokenization and whitespace

Tokenization uses exactly these whitespace bytes:

- space (`0x20`);
- form feed (`0x0c`);
- line feed (`0x0a`);
- carriage return (`0x0d`);
- horizontal tab (`0x09`);
- vertical tab (`0x0b`).

The checker creates tokens by splitting each output on sequences of one or more
consecutive whitespace bytes. If the submission and answer contain different
numbers of tokens, the checker returns `WA`.

When `whitespace_sensitive` is `false`, whitespace is used only for
tokenization and is otherwise ignored. Leading and trailing whitespace is
accepted, and any nonempty sequence of the six whitespace bytes between tokens
is equivalent to any other such sequence.

When `whitespace_sensitive` is `true`, every whitespace sequence must match
byte for byte in type and amount, including leading and trailing whitespace.
If all tokens are otherwise accepted but any whitespace sequence differs, the
checker returns `PE`.

#### Relationship with standard comparison

`STANDARD` and `OUTPUT_CHECKER` must use the same six whitespace bytes and the
same rule for splitting tokens. They intentionally retain different verdict
semantics. Adding `OUTPUT_CHECKER` must not change `STANDARD`, because doing so
could change historical results when existing submissions are rejudged. The
[default token validator guide](TOKEN_VALIDATOR.md#4-the-comparison-algorithm)
defines the complete `STANDARD` comparison contract.

The current `STANDARD` comparator applies these checks in order:

1. It returns `AC` for an exact byte match.
2. It returns `AC` when the only differences are trailing whitespace on lines,
   trailing blank lines, or newline representation.
3. It returns `PE` when the tokens are byte-identical but another whitespace
   sequence differs.
4. It returns `WA` when the tokens differ.

The `whitespace_sensitive` setting belongs only to `OUTPUT_CHECKER`. When it is
`false`, every token-equivalent whitespace difference is accepted as `AC`.
When it is `true`, a token-equivalent whitespace difference produces `PE`.
Different token values or token counts produce `WA` in either mode.

For example, given this expected output:

```text
10 20
```

and a submission containing five spaces between the tokens:

```text
10     20
```

`STANDARD` returns `PE`, `OUTPUT_CHECKER` with
`whitespace_sensitive=false` returns `AC`, and `OUTPUT_CHECKER` with
`whitespace_sensitive=true` returns `PE`.

For a submission that differs only by spaces after `20`, `STANDARD` and
`OUTPUT_CHECKER` with `whitespace_sensitive=false` return `AC`, while
`OUTPUT_CHECKER` with `whitespace_sensitive=true` returns `PE`.

#### Text comparison

When `case_sensitive` is `true`, corresponding text tokens must be identical
byte for byte. If they differ only by ASCII letter case, the checker returns
`PE`; any other textual difference is `WA`.

When `case_sensitive` is `false`, uppercase ASCII letters `A` through `Z` are
equivalent to their lowercase forms `a` through `z`. Every other byte is
compared exactly. This avoids locale-dependent and Unicode-version-dependent
results.

#### Floating-point comparison

Floating-point tolerances are optional, finite, non-negative decimal values.
When neither tolerance is configured, the checker compares every token as
text, including tokens that look numeric. For example, `1.0` and `1.00` are
different text tokens and produce `WA`.

When at least one tolerance is configured, the checker attempts numeric
comparison only when the answer-file token is a valid floating-point token. If
the answer token is numeric but the corresponding submission token is not,
the checker returns `WA`. If the answer token is not numeric, the checker uses
the text-comparison rules above. A configured tolerance of zero still enables
numeric comparison, so numerically equal values with different textual forms
are accepted.

The floating-point grammar is:

```text
sign        = "+" | "-"
digit       = "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9"
digits      = digit, { digit }
significand = digits, [".", { digit }] | ".", digits
exponent    = ("e" | "E"), [sign], digits
float       = [sign], significand, [exponent]
```

This grammar accepts values such as `1`, `-1.25`, `+.5`, `5.`, `1e9`, and
`-2.5E-10`. It does not recognize `NaN`, infinity, or another non-finite
spelling as a floating-point token.

For the tolerance formulas:

- `s` is the floating-point value parsed from the submission-output token;
- `a` is the floating-point value parsed from the answer-file token;
- `epsilon_abs` is the configured absolute tolerance;
- `epsilon_rel` is the configured relative tolerance.

An absolute tolerance accepts the token when:

```text
|s - a| <= epsilon_abs
```

A relative tolerance accepts the token when:

```text
|s - a| <= epsilon_rel * |a|
```

When both tolerances are configured, the token is accepted if either condition
holds. When `a` is zero, relative tolerance alone accepts only an exact numeric
zero; a problem that needs a neighborhood around zero must configure an
absolute tolerance.

Numeric parsing and arithmetic must use a representation with at least IEEE
754 binary64 precision and parse each token to the nearest representable value.
If a submission value is outside the configured tolerances, the checker
returns `WA`.

If every token comparison is accepted, the checker returns `AC` unless an
enabled case or whitespace rule produces `PE`. Any semantic mismatch, token
count mismatch, invalid submission number, or value outside tolerance produces
`WA`, which takes precedence over `PE`.

#### Package representation

The problem package is the source of truth for the validator type, validator
source, and checker semantics. A future package-format version must use the
required top-level `validator_type` discriminator with exactly one of these
lowercase values:

- `standard` selects NOCA's built-in comparator;
- `interactive` selects a concurrently running validator;
- `checker` selects a post-execution output checker.

The existing `custom_validator` object remains the source declaration for both
code-bearing validator types. It carries `language_id` and `source_file`.
Interactive validators and output checkers both store their single source file
under the archive-relative `validator/` directory. The source path must keep
the existing `validator/<name>` shape; a separate `checker/` directory is not
introduced. The `custom_validator` name is retained intentionally to minimize
package-schema churn; the new format must not introduce a parallel `validator`
or `output_checker` source-declaration key.

The valid field combinations are:

- `standard` requires no validator source, requires `custom_validator` to be
  `null`, and forbids `checker_semantics` and `validator/` members;
- `interactive` requires a non-null `custom_validator`, forbids
  `checker_semantics`, and uses the existing interactive test-case and sample
  interaction rules;
- `checker` requires a non-null `custom_validator`, requires
  `checker_semantics`, and uses normal input and optional opaque
  reference-output test-case data.

The validation strategy is immutable after the problem is created. The author
must select `standard`, `interactive`, or `checker` during problem creation, and
NOCA must reject every later attempt to change `validator_type`. Changing from
any strategy to another requires creating a new problem from scratch. This rule
applies equally to Web and Arena services, APIs, forms, package operations, and
administrative workflows.

Replacing or removing checker source does not change a problem's strategy. A
checker problem with no active `VALID` checker remains a checker problem but
cannot accept submissions. Its author may later upload another checker
candidate. Replacing the checker source, language, or semantics within the
`checker` strategy continues to use staged candidate promotion.

For example, a checker declaration is:

```json
{
  "validator_type": "checker",
  "custom_validator": {
    "language_id": "python3",
    "source_file": "validator/validator.py"
  },
  "checker_semantics": {
    "whitespace_sensitive": false,
    "case_sensitive": true,
    "float_relative_tolerance": "1e-6",
    "float_absolute_tolerance": null
  }
}
```

`checker_semantics` is a closed object with exactly the four keys shown above.
It has no omitted-field defaults: all four keys are required, and unknown keys
are forbidden. `whitespace_sensitive` and `case_sensitive` must be JSON
Booleans and cannot be `null`. Each tolerance must be either `null` or a decimal
string accepted by the floating-point grammar and restrictions above. `null`
explicitly means that the tolerance is unset; it is not a request for NOCA to
select a default.

Decimal strings prevent an import and export round trip from changing
tolerance values through binary floating-point conversion. Package import must
reject a missing required field, an unknown `checker_semantics` field, a wrong
type, an invalid value, an inconsistent `validator_type`, or a source-layout
mismatch before staging the validator. NOCA then persists the validated
semantics as part of the active checker configuration.

The package declares values but does not perform comparison. The checker source
must follow the comparison-authority contract above when its algorithm compares
contestant and answer tokens. A constraint-only checker may ignore settings that
do not apply to its algorithm. NOCA must not apply a second comparison after the
checker reports its verdict.

This contract requires a problem-package format-version bump. The current
package-format documentation remains unchanged until the other output-checker
contracts are settled and the version migration is designed. The future
migration must map packages without an interactive validator to `standard` and
packages with the existing `custom_validator` declaration to `interactive`;
it must never infer `checker` for an older package. Importing a package to create
a problem sets the new problem's immutable strategy from `validator_type`.
Importing or applying package data to an existing problem must reject a
different strategy rather than convert that problem.

#### Checker environment

At runtime, the Autojudge translates `checker_semantics` into a fixed checker
process environment. It also injects the same core values guaranteed to an
interactive validator:

- `PROBLEM_TIME_LIMIT`, the budget for the **whole test case** in milliseconds,
  which is `PROBLEM_TIME_LIMIT_PER_RUN` times `PROBLEM_REPETITIONS`;
- `PROBLEM_TIME_LIMIT_PER_RUN`, the time limit for **one run** of a test case;
- `PROBLEM_REPETITIONS`, how many times each case is run for the submitted
  language (`1` unless the problem sets a per-language row);
- `PROBLEM_OUTPUT_LIMIT`, the effective contestant-output limit in bytes;
- `PROBLEM_MEMORY_LIMIT`, the submitted language's effective memory limit in
  KiB;
- `PROBLEM_PID_LIMIT`, the submitted language's effective PID limit;
- `USER_LANGUAGE`, the submitted language ID.

`PROBLEM_TIME_LIMIT` reports the whole case's budget rather than the per-run
number deliberately: that is what it has always reported, and redefining it would
have made every already-deployed checker silently stricter. See
[the interactive validator guide](INTERACTIVE_VALIDATOR.md#limit-metadata-available-to-the-validator).

These values contain everything the checker receives about problem limits.
Web and Arena do not inject the limits of other languages. A checker's behavior
must not depend on which unrelated languages are enabled or on their configured
limits. The five values are metadata for the checker; they do not apply the
contestant's limits to the checker process. Their presence is guaranteed by
NOCA, although checker authors may handle missing values defensively when
running their source outside NOCA.

The comparison-policy environment-variable names are:

- `PROBLEM_OUTPUT_CASE_SENSITIVE`, always present;
- `PROBLEM_OUTPUT_WHITESPACE_SENSITIVE`, always present;
- `PROBLEM_OUTPUT_FLOAT_RELATIVE_TOLERANCE`, omitted when unset;
- `PROBLEM_OUTPUT_FLOAT_ABSOLUTE_TOLERANCE`, omitted when unset.

Boolean values are exactly the lowercase ASCII strings `true` and `false`.
Uppercase or mixed-case spellings, `1`, `0`, an empty value, and values with
leading or trailing whitespace are invalid.

When present, each tolerance value contains only ASCII characters and follows
the floating-point grammar defined above. It must parse as a finite,
non-negative value, may use decimal or exponent notation, and may be zero. It
must not contain leading or trailing whitespace. Although the grammar permits
a negative sign, a negative tolerance is invalid. A value that overflows to a
non-finite representation is also invalid. An unset tolerance is represented
only by omitting its variable; an empty variable is invalid.

These variables belong to the checker process environment. They are not NOCA
deployment settings and must not be read from the Autojudge host environment.
Using fixed names keeps the checker command stable, prevents packages from
injecting arbitrary environment variables, and gives every supported language
the same contract.

Each checker decides which variables its algorithm requires. NOCA always
injects the two Boolean variables. A tolerance key whose package value is
`null` produces no corresponding environment variable, while a non-null value
is injected verbatim after validation. A checker may require one or both
tolerances. It may ignore a setting only when the corresponding comparison does
not apply to its algorithm. If a variable required by the checker is missing or
malformed, the checker must abort with exit code `3`; it must not turn a
checker-contract error into a contestant verdict.

Package import validates package-owned values before activation. The checker
may validate its requirements defensively in case its runtime contract is
broken. These values remain inputs to the checker; NOCA must not apply a second
comparison after the checker reports its verdict.

## Source and build model

The first output-checker version must reuse the interactive validator's
single-source build model. A checker consists of exactly one non-empty,
non-blank UTF-8 source file whose encoded upload is at most 256 KiB. Multi-file
source bundles, prebuilt binaries, archives, and package-defined build commands
are not supported.

Any globally active Autojudge language may be used for the checker, even when
that language is not enabled for contestant submissions in the contest. An
unknown or inactive language makes the candidate invalid. NOCA uses the
selected language's registered source filename, compile image, compile command,
compile timeout, artifact rules, run image, and run command. The registered run
command is the checker entry point; the problem package cannot override it.

NOCA does not supply or inject a checker helper library. The source must be a
standalone program that uses its language's normal runtime facilities and the
NOCA checker contract. Example sources and templates may be added later, but
they are not runtime dependencies or part of the first-version contract.

NOCA persists only the checker language, source, semantics, revision metadata,
and validation diagnostics. It never retains or shares a compiled checker
artifact. Candidate validation discards its artifact after compilation. Each
submission, solution test, profiling run, rejudge, or recovered judging job
recompiles the active source exactly once and may reuse that job-local artifact
across the job's test cases. No artifact is cached across jobs or revisions.

## Candidate validation

Output-checker candidate validation must reuse the interactive validator's
compile-validation workflow. In this contract, **validated** means that the
source and metadata pass structural validation and the selected language's
normal compilation or syntax check produces its required artifact. Candidate
validation does not execute the checker.

Consequently, candidate validation does not read or run any problem test case,
does not require trusted expected outputs to be accepted, and does not accept
author-provided valid or invalid checker vectors. Those mechanisms could be
designed as a separate future feature, but they are not an activation gate for
the first version.

The candidate follows this lifecycle:

1. Validate the source, language identifier, `validator_type`, and complete
   `checker_semantics` object before staging.
2. Store the language, source, semantics, a fresh candidate token, and
   `PENDING` state, then commit that candidate before enqueueing its validation
   job on the profiling-priority queue.
3. Have the worker reload a candidate only when its token still matches and its
   state remains `PENDING`; a stale or superseded job becomes a no-op.
4. Compile the candidate through the ordinary Autojudge compile pipeline.
5. On success, conditionally promote the language, source, and semantics
   together, mark the active revision `VALID`, clear the candidate fields, and
   commit those changes atomically.
6. On a compiler rejection or compilation-limit violation, mark only the
   matching candidate `INVALID`, retain bounded compiler diagnostics, and leave
   any previous active revision unchanged.

Docker startup or communication errors, missing judge images, worker crashes,
queue failures, database or storage failures, and artifact-transfer failures are
infrastructure failures. They must not be reported as compiler rejections. The
worker must leave the matching candidate `PENDING`, preserve the active
revision, and let the existing queue recovery and reaper workflow retry the same
candidate token. If retry recovery is exhausted, NOCA marks the candidate
`INVALID` with a bounded diagnostic that states validation could not complete
after repeated internal failures. Detailed infrastructure diagnostics remain in
operator logs. An administrator can then retry validation or upload the
candidate again without first removing the active revision.

If staging cannot commit the candidate, the request fails and no candidate is
published. If enqueueing fails after the commit, the committed candidate stays
`PENDING` for reconciliation; an HTTP error alone is not the asynchronous
recovery mechanism. If a promotion transaction rolls back, neither the active
revision nor candidate state changes, and queue recovery may retry the job. No
partially promoted language, source, or semantics can become visible.

Compilation uses the same safeguards as interactive candidate validation: a
disposable network-disabled compile container, the selected language's compile
timeout, a 512 MB container memory limit, and a 128-PID container limit. The
retained compiler diagnostics are capped at 8,192 characters. There is no
checker watchdog or checker stdout/stderr limit during candidate validation
because the checker is never executed. Queue recovery and exhausted-validation
handling must also reuse the interactive candidate-job workflow so a
permanently abandoned job cannot leave a candidate `PENDING` forever.

Compilation alone cannot prove that a checker follows its runtime contract. A
promoted checker that later exits with code `3` or another undocumented clean
code produces the internal failure defined by the checker verdict protocol and
is not crash-retried; its active revision remains unchanged. A promoted checker
that crashes follows the one-retry and second-failure containment policy
defined above. A checker that returns a documented verdict while applying the
comparison policy incorrectly is a checker defect that NOCA cannot detect
automatically. Candidate validation is not rerun in any of these cases.

## Persistence and diagnostics

Output checkers must reuse the interactive validator's attempt-retention,
byte-capping, and text-decoding rules. They also retain the standard workflow's
per-test-case contestant execution results. Checker diagnostics remain separate
from contestant execution diagnostics so that each failure keeps the correct
owner.

### Records and attempt retention

For each executed test case, NOCA persists the normal contestant execution
result, including the existing repetition aggregates and bounded contestant
stdout and stderr excerpts. It also persists one output-checker attempt row for
each checker process attempt. Web Contest and Arena use equivalent domain tables
owned by the submission judgment.

Each checker-attempt row must contain:

- the judgment ID, test-case ordinal, and attempt number (`1` or `2`);
- the checker exit code and signal;
- the checker wall time, memory use, and stdout byte count;
- the bounded checker stdout and stderr excerpts;
- whether the checker stdout excerpt was truncated;
- either the clean checker verdict or a typed staging, crash, or contract-failure
  reason; and
- the attempt creation time.

The contestant execution result is the per-test-case record. The checker row is
the per-attempt record. NOCA must not combine them because a checker retry must
not duplicate or replace the contestant execution result.

A judgment retains checker-attempt rows only for the last executed test case,
with at most two rows. Recording attempt 1 for a new case clears every checker
attempt from the previous case. Recording attempt 2 replaces only an existing
attempt-2 row, which makes a recovered duplicate write idempotent.

When attempt 1 crashes and attempt 2 returns a valid verdict, both rows remain
while that case is the last executed case. If an `AC` result advances judging to
another case, its attempt 1 clears those rows. Both rows survive when that case
is the final executed case. Unlike the interactive UI, the output-checker UI
does not hide a clean final `AC` attempt because checker output is part of the
documented validation feedback.

An input-staging failure is also persisted under the current case and attempt
number, with null checker process fields and a typed staging-failure reason. A
transient staging retry may therefore leave two rows under the same retention
rules. The existing contestant execution result remains unchanged.

Solution tests, profiling, and Auto-Limit must use equivalent attempt ownership
and last-case retention in their own result records. They must not write a
scoring submission judgment merely to retain checker diagnostics.

### Captured stream limits

Checker stdout retains its first 256 KiB of raw bytes per attempt. The capture
stores a `stdout_truncated` flag when the checker produces more data. The UI
renders a truncation notice from that flag; NOCA does not append a marker to the
stored text. The cap affects only retained diagnostics: NOCA continues draining
the stream so diagnostic capture cannot block the checker or change its verdict.

Checker stderr retains its first 16 KiB of raw bytes per attempt. As with the
interactive validator's stderr excerpts, NOCA stores no truncation flag and
appends no marker. A stored stderr value of exactly 16 KiB is therefore not
proof that the checker produced no additional bytes.

Both limits are byte limits applied before text decoding. They are independent
of the contestant output limit and do not limit the complete contestant output
temporarily staged as `/data/contestant.out`. The staged contestant output is
destroyed with the checker workspace. Only the bounded contestant excerpts from
the normal per-test-case result remain afterward.

### Text decoding

Before storing checker stdout or stderr in a database text column, NOCA decodes
the retained bytes as UTF-8 with replacement. Each invalid byte sequence becomes
the Unicode replacement character (`U+FFFD`). NOCA then removes every NUL
character (`U+0000`) because PostgreSQL text columns cannot store it. Byte caps
are calculated before this conversion.

### Diagnostic visibility

For clean checker exits `0`, `1`, and `2`, the submitting contestant can see the
bounded stdout as **Validation result** and the bounded stderr as **Validation
errors**. Contest admins and judges, Arena admins, authorized Arena teachers,
and problem owners with management permission can see the same diagnostics
when their existing submission or problem permissions grant access. Operators
can inspect them through operational database and service-log access. This
contract does not introduce a separate operator-facing application role or UI.
Other contestants can never see them.

For input-staging failures, checker contract failures, crashes, timeouts, and
resource failures, the submitting contestant receives only a generic internal
judging failure. Detailed checker stdout, stderr, exit information, and typed
failure reasons are visible only to authorized admins, judges, problem
owners with management permission, and operators. Arena teachers without
problem-management authority do not receive internal checker details merely
because they may view a student's submission.

Validator-source access is separate from attempt diagnostics. A submission
owner never receives checker source through a diagnostic view. An authorized
administrative download can expose only the current active checker under the
current-source labeling rules in the checker lifecycle; it cannot recover the
source used by a historical judgment.

### Snapshot policy

The initial implementation deliberately follows the interactive validator's
simplified history model. A completed judgment does not persist a snapshot or
immutable reference to the checker source, checker language, checker semantics,
checker revision, test-case input, or reference output. The checker-attempt row
stores only the test-case ordinal and its bounded diagnostics and measurements.

The complete last-repetition contestant output, staged test-case input, staged
reference output, compiled checker artifact, and checker workspace are
ephemeral. The normal per-test-case result retains only its configured bounded
contestant-output excerpts. As a result, replacing checker configuration or
test-case data can make the exact historical run impossible to reconstruct.
Rejudging remains the mechanism for applying the current active configuration.
Immutable revision and test-data pinning may be added in a future release.

## Checker lifecycle

Output checkers must reuse the interactive validator's source, build,
candidate-publication, and rollback workflow. Their validation strategy,
persisted semantics, and runtime contract remain distinct. The initial design
must provide:

- candidate upload and validation before activation;
- one current active source, language, semantics, and revision state, loaded
  together when a judging job is dispatched;
- safe replacement that keeps the previous active revision when a candidate
  fails;
- checker removal that leaves the immutable `checker` strategy intact and
  blocks submissions until another candidate becomes active;
- rejection of every post-creation validation-strategy change;
- language and source validation shared by Web and Arena;
- package, backup, restore, and public-export rules that preserve the strategy
  without exposing private checker source.

The implementation may reuse domain-neutral lifecycle utilities. It must not
reuse interactive-only assumptions such as input-only test cases, concurrent
processes, transcripts, or interactive verdict classification.

NOCA does not retain historical checker source, semantics, or immutable revision
records for completed judgments. Candidate promotion replaces the previous
active configuration. A completed judgment retains its results and diagnostics
but cannot identify or reconstruct the checker configuration that produced
them. Any administrative download associated with a historical submission must
be labeled as the current active checker, not as the checker that judged that
submission. If no active checker exists, no source is available for download.

Rejudging or explicitly requeueing a submission uses the active checker
configuration loaded at the new job's dispatch time. Persistently pinning each
judgment to a checker revision may be added in a future release but is outside
the initial contract.

## Isolation and failure ownership

The checker is trusted problem-author code, but it still requires isolation. It
must run without network access, with bounded resources, and without access to
unrelated submissions or test cases. Each invocation must receive only the
files and metadata required to validate the current case.

NOCA must distinguish these outcomes:

- The contestant fails before validation: preserve the contestant verdict.
- NOCA cannot stage a required checker input: report the internal job failure
  defined above without changing the checker revision or problem state.
- The checker exits with `0`, `1`, or `2`: report `AC`, `WA`, or `PE`,
  respectively.
- The checker crashes, times out, exceeds its limits, or violates its contract:
  after the applicable retry policy, report an internal judging failure and
  retain operator diagnostics.

For a valid checker verdict, bounded stdout and stderr are contestant-visible.
For an internal validator failure, both streams are judge- and operator-only.
The implementation must bound every captured stream because checker feedback
can contain secret test information or grow without limit.

## Initial scope

The first implementation must focus on the native NOCA workflow:

- one explicit `OUTPUT_CHECKER` strategy shared by Web and Arena;
- immutable strategy selection during problem creation, enforced by Web, Arena,
  shared services, and package operations;
- checker candidate compilation and activation;
- the fixed `/data` file ABI, ephemeral checker storage, and problem-limit
  environment contract;
- persisted comparison policy passed to every checker invocation;
- post-execution validation for submissions, solution tests, profiling,
  Auto-Limit, rejudging, and recovered jobs;
- normal per-test-case result persistence;
- interactive-equivalent runtime safeguards, crash retry and containment, and
  checker-container reuse within one judgment, including judgment-only failure
  in Contest and revision/problem disablement in Arena;
- bounded checker diagnostics and internal-failure handling;
- authoring, enablement, import, export, backup, and restore invariants.

Multi-file checker bundles, scoring checkers, and specialized checker templates
can be considered separately after the core strategy is stable.

## Next steps

Write an implementation plan that resolves the remaining contracts before code
changes begin. At minimum, it must define the validation-strategy schema,
comparison-policy persistence and transport, implement the settled diagnostic
persistence model, and define migration of existing problems.

The Web and Arena implementation backlog must add strategy selection to problem
creation and make the selected strategy read-only afterward. Edit workflows
must support source and semantics replacement only within the problem's existing
strategy and must reject cross-strategy package updates.

Only after those contracts are settled may the package format version be
bumped and `PROBLEM_PACKAGE_FORMAT.md` be updated with `validator_type`, the
shared `validator/` source layout, and `checker_semantics`.

The implementation plan must cover Web submissions, Arena submissions,
solution tests, profiling, rejudging, queue recovery, and focused real-sandbox
tests for acceptance, rejection, checker crashes, checker timeouts, one-retry
recovery, second-failure containment, and container reuse.
