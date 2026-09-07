# Default token validator

NOCA's default validator runs a submission to completion and compares its
standard output with the test case's expected output. It requires no
problem-specific validator program. This guide explains the complete comparison
contract, including which whitespace differences produce `AC`, `PE`, or `WA`.

This guide uses `TOKEN_VALIDATOR` to name that default path. The repository also
calls it the built-in comparator or the `STANDARD` validation strategy. The
comparison itself is implemented by
[`compare_output()`](../../autojudge/verdict.py). It is available for both
Contest problems in the Web module and Arena problems.

Related references:

- [Submission data flow](../DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md) explains
  where comparison occurs in the submission lifecycle.
- [Autojudge infrastructure](../../autojudge/docs/AUTOJUDGE_INFRA.md) describes
  worker isolation, execution limits, and queue recovery.
- [Problem package format](../PROBLEM_PACKAGE_FORMAT.md) defines test-case
  files, normalization, samples, and package limits.
- [Interactive validator guide](INTERACTIVE_VALIDATOR.md) covers problems in
  which a validator and contestant communicate while both are running.
- [Output checker validator rationale](OUTPUT_CHECKER_VALIDATOR.md) covers the
  planned strategy for accepting multiple semantically correct outputs.

## 1. How judging works

The token validator is the final step of the normal compile-and-run path. It
only compares output after the contestant program exits normally within every
execution limit.

For a real Contest or Arena submission, Autojudge performs these steps:

1. Load the problem, ordered test cases, expected outputs, language, and
   effective limits.
2. Compile the contestant's source. A compilation failure ends the judgment
   with `CE` before any test case runs.
3. Acquire an isolated, network-disabled run container for the contestant.
4. For each test case in ordinal order:
   1. Start a fresh isolated process with the test-case input on standard input.
   2. Run every configured repetition under the test case's shared execution
      budget.
   3. Stop with an execution verdict when the program exceeds a limit, crashes,
      or exits abnormally.
   4. For a normal exit, compare the complete bounded standard output with the
      expected-output file.
   5. Record the case result. Continue only when the verdict is `AC`.
5. Return `AC` only when every test case and every configured repetition is
   accepted.

The first non-`AC` case decides a real submission. Later cases do not run and
receive no result. This includes `PE`: a presentation error is not accepted by
the default validator.

The validator does not inspect standard error, source code, execution time, or
memory usage to decide output correctness. Those values belong to execution
and diagnostics. It compares only the contestant's standard-output bytes with
the expected-output bytes.

## 2. Configuring a problem

The token validator has no source file, language, compile step, runtime, or
validator-specific setting. A problem uses this path when its stored validation
strategy is `standard` — not merely because it happens to hold no validator
source. A problem stored as `interactive` whose source was removed is *not*
judged by the token comparator; it is refused as non-judgeable until an active
valid revision exists.

Each test case must contain both of these files:

- an input file, which Autojudge writes to the contestant's standard input;
- an expected-output file, which Autojudge compares with the contestant's
  standard output.

An expected-output file is mandatory, but it may be empty. A present, empty
file means that the correct answer has no tokens. A missing file is invalid
judge data: submission preflight blocks it, or a queued judgment that reaches
the worker fails internally. It is not treated as an empty answer or as
contestant `WA`.

Standard problems may mark test cases as sample or secret. A sample case shows
its input and expected output to contestants. A secret case remains available
only to authorized staff and Autojudge. An optional explanation can document
why a sample has its expected output.

## 3. Test-case storage and normalization

NOCA treats test-case input and expected output as UTF-8 text at every authoring
and import boundary. It stores each standard case as a numbered pair under the
configured test-case root.

The on-disk pair has this form:

```text
NNN.in
NNN.out
```

The number is the zero-padded, one-based ordinal. Autojudge loads cases in
ordinal order and refuses a set with no input files or an input without its
matching output.

When NOCA saves or imports a test case, it converts CRLF and lone CR line
endings to LF. Autojudge repeats this normalization while loading legacy files.
Empty content and empty lines are preserved; NOCA does not trim test-case
content at the storage boundary.

A full problem package carries every standard case as matched `in/NNN.in` and
`out/NNN.out` members. A public package carries only sample pairs. Package
imports also accept `.sol` as an output suffix and remap source ordinals into a
contiguous sequence. See the [test-case package contract](../PROBLEM_PACKAGE_FORMAT.md#test-cases)
for archive limits and complete pairing rules.

Editing, adding, removing, or reordering test cases does not change completed
judgments automatically. Rejudge existing submissions when they must use the
new case set or expected output.

## 4. The comparison algorithm

The comparator uses raw bytes. It does not decode contestant output, apply
Unicode normalization, fold letter case, parse numbers, or call a locale-aware
comparison function.

It applies the following checks in order:

1. Return `AC` when contestant output and expected output are byte-identical.
2. Normalize line endings and trailing whitespace in both outputs. Return `AC`
   when the normalized byte strings match.
3. Split both outputs into whitespace-delimited tokens. Return `PE` when the
   token sequences are byte-identical.
4. Return `WA` when the token sequences differ.

The order matters. A result accepted by an earlier check never reaches a later
one. In particular, token equality alone does not always produce `AC`.

The comparison function has a strict internal mode in which only an exact byte
match is `AC` and every difference is `WA`. The current submission runner never
selects that mode, and problem authors cannot configure it. All standard
judgments use the four-step behavior documented here.

### Exact-byte comparison

The first check is deliberately simple. Every byte, including whitespace and
line endings, must occur at the same position in both outputs.

This fast path returns `AC` immediately. The remaining checks exist only to
classify outputs that are not exact matches.

### Line and trailing-whitespace normalization

The second check accepts common differences at the ends of lines without
changing the content or indentation at the start and middle of a line.

For each output, the comparator:

1. Splits lines at LF, CR, or CRLF boundaries.
2. Removes trailing ASCII whitespace from every line.
3. Removes trailing lines that are empty after that trimming.
4. Joins the remaining lines with LF.

This normalization means that the following differences are accepted as `AC`:

- LF, CRLF, or CR line endings;
- a missing or present final line ending;
- spaces or tabs after the last non-whitespace byte on a line;
- blank or whitespace-only lines after the last content line.

The normalization does not remove leading whitespace, collapse whitespace
between tokens, reorder lines, or change any non-whitespace byte. A difference
in one of those positions proceeds to token comparison.

### Token comparison

The third check determines whether the answer is textually correct but has a
different whitespace structure. A token is a maximal non-whitespace byte
sequence.

Tokenization recognizes exactly these ASCII whitespace bytes as delimiters:

- horizontal tab (`0x09`);
- line feed (`0x0a`);
- vertical tab (`0x0b`);
- form feed (`0x0c`);
- carriage return (`0x0d`);
- space (`0x20`).

One or more consecutive delimiter bytes form one separator. Leading and
trailing whitespace creates no empty token. All other bytes remain part of a
token.

Token sequences must have the same length, values, and order. Comparison is
case-sensitive and byte-for-byte. Therefore:

- `YES` and `yes` are different tokens;
- `1`, `01`, `1.0`, and `1.00` are different tokens;
- `1 2 3` and `3 2 1` have different token order;
- an extra or missing token produces a different sequence.

The default validator has no floating-point tolerance. A problem that expects
a numeric value must define the exact accepted textual token in each expected
output. The planned output-checker strategy exists for problems that need
numeric tolerances, case-insensitive matching, unordered answers, or multiple
semantically valid constructions.

### Examples

The following examples use expected output `10 20` followed by LF. They show
which comparison stage decides each result.

| Contestant output | Verdict | Reason |
| --- | --- | --- |
| `10 20` followed by LF | `AC` | Exact byte match. |
| `10 20` followed by CRLF | `AC` | Only the newline representation differs. |
| `10 20   ` followed by LF and blank lines | `AC` | Only trailing whitespace differs. |
| `10     20` followed by LF | `PE` | Tokens match, but internal spacing differs. |
| A leading space followed by `10 20` and LF | `PE` | Tokens match, but leading spacing differs. |
| `20 10` followed by LF | `WA` | Token order differs. |
| `10 20 30` followed by LF | `WA` | The contestant printed an extra token. |
| `10 020` followed by LF | `WA` | Numeric-looking tokens are still raw bytes. |

For an empty expected-output file, empty contestant output is an exact `AC`.
Whitespace-only contestant output is also `AC` because trailing-whitespace
normalization reduces both sides to the same empty byte string. Any
non-whitespace token produces `WA`.

## 5. Verdicts and precedence

Output comparison can produce only `AC`, `PE`, or `WA`. Compile, sandbox, and
resource failures are classified before the comparator runs.

The relevant outcomes are:

- `CE`: the source did not compile, so no test case ran.
- `TLE`: the contestant exceeded its execution-time budget.
- `MLE`: the contestant exceeded its memory limit.
- `OLE`: standard output reached the effective output ceiling.
- `RE`: the contestant exited abnormally, crashed, or was terminated by a
  runtime signal.
- `WA`: the contestant's token sequence differed from the expected sequence.
- `PE`: the tokens matched, but non-tolerated whitespace differed.
- `AC`: execution succeeded, and the output passed the exact or normalized
  comparison.

The effective stdout ceiling is the smaller of the problem or per-language
limit and Autojudge's global hard ceiling. When output reaches that ceiling,
Autojudge returns `OLE` without asking the token validator to compare a
truncated prefix.

Likewise, `TLE`, `MLE`, and `RE` are execution results. Output that happens to
match before one of those failures does not override the failure.

## 6. Repetitions and related workflows

A standard problem can configure multiple repetitions for a language. The
current token-validator path executes and compares every repetition
independently.

For one test case, Autojudge:

1. Starts with the effective time budget for that case: the stored per-run time
   limit multiplied by the repetition count.
2. Runs the contestant up to the configured repetition count.
3. Subtracts each observed run time from the remaining shared budget.
4. Stops immediately when a repetition is not `AC` or no time remains.
5. Reports total wall time and peak memory, output, and process usage across the
   repetitions that ran.

Because the budget is shared rather than re-imposed per run, a slow repetition
borrows from a fast one: `TLE` means the *average* run exceeded the stored limit,
not that a single one did.

Every repetition must therefore produce accepted output. Nondeterministic code
that prints a wrong or differently formatted answer on any repetition fails the
case.

The surrounding workflows use the same comparator with different traversal
goals:

- Real Contest and Arena submissions stop at the first non-`AC` test case.
- Standard solution tests run every test case so judges and administrators can
  inspect the complete non-scoring result set.
- Profiling and Auto-Limit require every repetition and every case to be `AC`;
  a different verdict rejects the reference implementation instead of
  publishing calculated limits.
- Rejudging and recovered jobs use the same comparison rules as a new judgment.

## 7. Limits and isolation

The token validator does not execute as a separate process, so it has no
validator-specific time, memory, process, or output limit. It is a pure
in-worker comparison over already bounded byte strings.

The contestant still runs under all normal judge limits:

- CPU time and wall-clock timeout;
- cgroup memory;
- process and thread count;
- standard-output bytes;
- the outer Docker safety limits.

Each run is network-disabled and executes through `isolate`. Autojudge resets
the sandbox artifacts before each repetition. The same judgment-local compiled
artifact can be reused across cases, but a test case must not depend on files or
process state created by a previous case.

Standard error is bounded and may be retained for diagnostics, but it never
participates in token comparison. Writing the correct answer to standard error
instead of standard output produces `WA` when standard output lacks the
expected tokens.

## 8. Failure handling and retries

NOCA separates contestant outcomes from judge-side failures. A missing case,
missing expected-output file, filesystem/database mismatch, or failure to
acquire a run container is an internal judgment failure, not contestant `WA`,
`RE`, or `CE`.

When Autojudge recognizes a recoverable `isolate` runtime failure, it destroys
the affected container, acquires another one, and retries the current test case
once. The retry reruns every configured repetition for that case. A repeated or
non-recoverable infrastructure failure marks the judgment `FAILED` and leaves
detailed information in operator diagnostics.

Queue reconciliation and stale-job recovery may dispatch the judgment again.
Attempt-token fences prevent an older worker attempt from overwriting results
owned by a replacement attempt. Retrying infrastructure work never changes the
comparison policy.

## 9. Diagnostics

The judge stores enough bounded data to explain normal execution failures
without treating diagnostic excerpts as the authoritative output used for
comparison. The comparator reads the complete output up to the effective
output ceiling; the UI receives only configured excerpts.

Depending on the workflow, diagnostics can include:

- compiler output for `CE`;
- the test-case verdict and ordinal;
- wall time, memory, output size, process usage, exit code, and exit signal;
- bounded contestant standard-output and standard-error excerpts;
- for a standard solution test, bounded input, expected-output, and contestant
  output snapshots for every case.

The standard solution-test snapshots are capped at 10 KiB per input,
expected-output, and contestant-output field. General submission stdout and
stderr excerpts use the configured Autojudge excerpt limits. These excerpts
may be shorter than the data that the comparator evaluated.

## 10. Contract at a glance

The default behavior can be summarized without reference to a validator source
or protocol.

- Strategy name: `TOKEN_VALIDATOR`, also called `STANDARD`.
- Validator source: none.
- Test-case data: one input and one mandatory expected output per case.
- Comparison data: raw, bounded contestant stdout and expected-output bytes.
- Accepted tolerance: line-ending differences, trailing whitespace on lines,
  and trailing blank lines.
- Presentation error: identical ordered tokens with another whitespace
  difference.
- Wrong answer: different token value, count, case, spelling, or order.
- Numeric behavior: no parsing and no tolerance.
- Submission traversal: ordinal order, stopping at the first non-`AC` case.
- Problem domains: Contest and Arena.
