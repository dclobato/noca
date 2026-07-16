# Custom Interactive Validators

A custom validator turns a problem into an **interactive** problem: instead of comparing the
contestant's output against a fixed expected-output file, NOCA runs a program you supply — the
validator — and lets the two processes talk to each other. The validator decides the verdict
and reports it through its **process exit code**.

The validator is **parametrized by test-case input**: the judge replays the conversation once
per test case, feeding that case's input to the validator's stdin before the two sides start
talking. One validator source therefore plays many rounds — one per case — the same way a
Kattis interactive problem does.

Both problem domains support this: Contest problems (web module) and Arena problems.

Related references:
- [DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md](DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md) — where the interactive branch sits in the submission lifecycle
- [../autojudge/docs/AUTOJUDGE_INFRA.md](../autojudge/docs/AUTOJUDGE_INFRA.md) — worker isolation and the queue protocol
- [CONFIG.md](CONFIG.md) — `NOCA_JUDGE_CUSTOM_VALIDATOR_WATCHDOG_SECONDS` and the container limits

## Table of Contents

- [1. How judging works](#1-how-judging-works)
- [2. Setting a validator on a problem](#2-setting-a-validator-on-a-problem)
- [3. Test cases and sample interactions](#3-test-cases-and-sample-interactions)
- [4. Writing a validator](#4-writing-a-validator)
  - [The algorithm](#the-algorithm)
  - [Reference implementations](#reference-implementations)
  - [The rules behind it](#the-rules-behind-it)
- [5. Exit codes and verdicts](#5-exit-codes-and-verdicts)
- [6. Which limits are enforced, and by whom](#6-which-limits-are-enforced-and-by-whom)
- [7. Failure handling and retries](#7-failure-handling-and-retries)
- [8. Diagnostics](#8-diagnostics)
- [9. Limits and constants at a glance](#9-limits-and-constants-at-a-glance)

## 1. How judging works

For each submission to a validator problem, the Autojudge:

1. Loads the problem's **active** validator revision and compiles it. The validator is
   recompiled for every submission; binaries are never cached.
2. Compiles the contestant's source. (A contestant compile failure is still a normal `CE`.)
3. Acquires **two** fresh, network-disabled run containers — one for the contestant, one for
   the validator — and copies both compiled artifacts in. The same pair judges the whole
   submission.
4. Then, **for each test case in ordinal order**:
   1. Starts a fresh process on each side and wires them together: contestant stdout →
      validator stdin, validator stdout → contestant stdin. EOF (half-close) propagates in
      both directions.
   2. Writes the **test case's input** to the validator's stdin, ahead of anything the
      contestant says. The validator reads it to learn which round it must play (the secret
      number, the hidden graph, the query budget — whatever the problem needs).
   3. Runs the conversation to completion and turns the validator's exit code into that
      case's verdict.
   4. If the case is `AC`, moves on to the next case. **Otherwise the iteration stops** and
      that case's verdict is the submission's verdict.
5. Releases both containers. A submission whose every case ended `AC` is `AC`.

So the first case that does not pass decides the submission, exactly as a non-interactive
problem's first failing case does.

Interactive judging **never reads expected-output files** — only inputs. The rest of the
protocol is generated live by the validator. Consequences:

- Every problem needs **at least one test case**, interactive or not: the validator has
  nothing to play without one. The UIs refuse to create a problem, enable it, or accept a
  submission to it otherwise.
- A validator problem's test cases hold **input, sample flag and explanation only** — no
  expected output. The editors hide the expected-output field, and the packages, ZIPs and
  downloads for such a problem carry `.in` files only.
- Its cases may be **secret** like any other problem's. Only `is_sample` cases appear on the
  statement, showing the input and the explanation (there is no output to show).
- No `test_results` rows are produced. The per-attempt record is an
  interactive-attempt row instead (see [Diagnostics](#8-diagnostics)).

## 2. Setting a validator on a problem

The validator is a single **UTF-8 source file** in any globally active Autojudge language —
not necessarily a language enabled for the contest. NOCA compiles it with that language's
normal compile command.

**Arena:** on the problem edit page, the *Custom interactive validator* card. Pick a language,
upload the source, submit. New problems can also carry a validator straight from the create
form.

**Contest:** the same card on the problem edit page (`/c/{slug}/admin/problems/{id}/edit`),
plus the equivalent fields on the new-problem form.

In both cases the upload only stages a **candidate**. The application commits it, then queues
a compile-validation job on the Autojudge profiling-priority queue; the page polls for the
result:

- **Compiles** → the candidate is promoted to the active `VALID` revision. The problem can now
  be enabled and accept submissions.
- **Fails to compile** → the candidate is kept as `INVALID` with the compiler diagnostics shown
  on the page. An older active revision, if any, keeps working — a bad replacement never breaks
  a problem that was already judging.

While a validator is configured but not `VALID`, submissions to that problem are refused, and
an Arena problem cannot be enabled.

**Replacing a validator:** remove the current one first, then upload the new one. **Removing**
a validator clears both the active and candidate revisions; any in-flight validation job for
the removed candidate is discarded harmlessly (its token no longer matches).

Removing a validator also leaves the problem's [sample interactions](#3-test-cases-and-sample-interactions)
with nothing to illustrate, so the removal is confirmed through a modal that makes you choose:
**keep** them (they are hidden, and resurface if you add a validator again) or **delete** them
permanently. The endpoints require that choice as an exact `keep_interactions=true|false`; there
is no default.

**Downloading:** the problem edit page offers the current validator source; the submission
review/detail pages offer the validator that judged that submission.

**Problem packages:** an export of a validator problem carries `validator/validator<ext>`
(e.g. `validator/validator.py`, named for the validator's language) plus a
`custom_validator: {language_id, source_file}` block in `problem.json`, ships its test cases as
`in/NNN.in` files with **no** `out/NNN.out` — there is no expected output to export — and ships
its sample interactions as `interaction/NNN.interaction` / `interaction/NNN.explain`. Importing
such a package stages the validator as a fresh candidate (it is compiled and validated on
import, exactly like an upload), imports the inputs as secret cases, and imports the
interactions. A package with **no** validator has its `interaction/` folder dropped; a validator
package with no interactions imports fine but warns you that the problem shows no examples.
Public exports never include validator source, but they *do* include the sample interactions —
those are the public examples. See
[PROBLEM_PACKAGE_FORMAT.md](PROBLEM_PACKAGE_FORMAT.md).

## 3. Test cases and sample interactions

An interactive problem's test cases work differently from a plain problem's, and its public
examples are a different thing entirely.

### Test cases: input only, and always secret

Each case carries **input and an optional explanation, no expected output**. The input is written
to the validator's stdin before the conversation starts, so it *parametrizes* one round rather
than declaring an answer; the validator decides the verdict.

Every case is **secret**. A bare input reveals a secret without showing the contestant what to do
with it, so there is nothing worth publishing. The rule a problem with a configured validator must
satisfy is therefore:

> **zero public test cases, and at least one secret one.**

The application keeps it that way from several directions: staging a validator demotes any
existing public case to secret, the sample/secret toggle is refused while a validator is
configured, new cases are forced secret, and no edit path may remove the last secret case.

The "at least one secret case" half is a **gate**, not a write barrier. Arena's create form
legitimately stages a validator on a brand-new *disabled* problem that has no cases yet, so an
incomplete draft is allowed to exist. It is only forced to be complete where it would become
visible or judgeable: the Arena enable gate, and the submission preflight in both modules.

### Sample interactions: the public examples

What contestants see instead of sample cases are up to **five sample interactions** — worked
transcripts of the conversation a correct program has with the validator. They are authored on the
problem edit page in a card that sits above the test cases, one message per line, using the exact
two-character prefixes `> ` (a line the validator sends) and `< ` (a line the contestant's program
sends back):

```
> 3
< 5
> !8
```

The parser is strict: every line must carry one of those prefixes **including the space**. Bare
`>` / `<`, leading whitespace, and blank lines are rejected, naming the line to fix. An empty
protocol line is written as `> ` with nothing after it. Each interaction may carry an optional
Markdown explanation, shown below the conversation.

The transcript is stored in the same JSON shape the judge records for a real interactive attempt,
so the problem page renders your authored examples with exactly the UI the submission page uses
for the conversation that actually happened.

Sample interactions are managed with the same UX as test cases — pending add rows, pending removal
with undo, and drag-to-reorder — and, like test cases, they are only persisted when you press
**Save**.

The five-interaction cap is judged against the row count the *save* will produce, not the one
currently on screen: a save applies removals before additions. So if you are already at five and
mark one for removal, the **Add** button frees up immediately and you can write its replacement in
the same edit. A save that still would not fit is rejected before it changes anything.

## 4. Writing a validator

Every validator, in every language, follows the same skeleton. Read [The algorithm](#the-algorithm)
for the steps, then the rest of this section for the rules behind them.

### The algorithm

A validator plays one round, from the top, on a fresh process. Do these seven things in order:

1. **Name your exit codes.** They are the whole verdict vocabulary, so give them names rather
   than scattering `exit(1)` through the file: `AC = 0`, `WA = 1`, `TLE = 2`, `RE = 3`, `PE = 4`.
   Write one `finish(code, message)` helper that optionally prints `message` to stderr and then
   exits with `code`; every decision below is one call to it.
2. **Read the test case from stdin.** This is the round's secret data — the number to guess, the
   hidden graph, the query budget. Read *exactly* as many bytes as the case holds and not one
   more, because the contestant's first message is already queued behind it on the same stream.
3. **Optional: read the limits from the environment.** `PROBLEM_TIME_LIMIT`, `USER_LANGUAGE`, and
   the rest (see [Limit metadata available to the validator](#limit-metadata-available-to-the-validator))
   let you scale a query budget to the submitted language instead of hard-coding one. Treat every
   one as optional: read it, and fall back to a default when it is unset.
4. **Open the conversation.** Print whatever the protocol says the contestant hears first — the
   upper bound, the board size, the number of queries it gets — and **flush**.
5. **Loop over the contestant's messages.** For each line it sends:
   1. **Check for the final answer first** (say, a line starting with `!`). Parse it; a malformed
      answer is `RE`. A correct one is `AC`, a wrong one is `WA` — and either way, this round is
      over, so exit here.
   2. **Charge the query against the budget.** If the contestant has now exceeded the number of
      queries the case allows, exit `TLE`. The judge does not do this for you (see
      [Which limits are enforced, and by whom](#6-which-limits-are-enforced-and-by-whom)).
   3. **Parse the query.** Garbage the protocol does not permit is `RE` (or `PE`, if it is merely
      malformed rather than nonsensical).
   4. **Answer it, and flush.**
6. **Handle end of input.** If the contestant crashes or closes its output, your read hits EOF.
   Exit with a verdict code — do not fall off the end of the loop or let an EOF exception escape.
7. **Never exit any other way.** Every path out of the program is a `finish(...)` call.

In pseudocode:

```text
AC, WA, TLE, RE, PE = 0, 1, 2, 3, 4

max_value, max_tries, secret = read three lines of case input
limits = read environment (optional)

print(max_value); flush                  # open the conversation

for iteration, line in contestant messages:   # ends at EOF
    if line starts with "!":
        answer = parse(line[1:]) or finish(RE, "Invalid data")
        finish(AC) if answer == secret else finish(WA, f"!{secret}")

    if iteration > max_tries:
        finish(TLE, "Exceeded number of allowed iterations")

    guess = parse(line) or finish(RE, "Invalid data")
    print("<" if secret < guess else ">="); flush

finish(RE, "Unexpected flow")             # the contestant stopped talking
```

### Reference implementations

`sample_question/custom_validator/` holds this exact validator — the guess-the-number game above —
written out in **every language the judge supports**, as `sample-validator.<ext>`. They are line-
for-line equivalents: same exit codes, same environment dump to stderr, same protocol. Start from
the one in your language rather than from a blank file.

The same directory holds `main.<ext>`, the *contestant* side of the same game, which is what you
want when you are testing a validator you just wrote.

<!-- prettier-ignore -->
> [!WARNING]
> A sentinel loop over your language's line-reading function is a classic way to get this wrong.
> Python's `input()`, for one, signals end of input by **raising `EOFError`**, never by returning
> `None` — so `iter(lambda: input().strip(), None)` never ends, and the `EOFError` escapes as exit
> code 1, which the judge reads as a contestant `WA`. Test for end of input explicitly, and make
> that path exit with the code you meant.

### The rules behind it

The validator is an ordinary program, run once per test case. It:

- **first reads the test case's input from stdin** — this is what makes each round different,
- then reads the contestant's messages from that same **stdin**,
- writes its messages to the contestant on **stdout**,
- may write anything it likes to **stderr** — this is never shown to the contestant, only
  retained as a diagnostic for staff,
- ends by **exiting with the code that expresses the verdict** (next section).

Three practical rules:

- **Make the case input self-delimiting, and consume exactly it.** The case's bytes and the
  contestant's first message arrive on the *same* stream, back to back. Your validator must
  know where the input ends — read a fixed number of lines, or lead with a count — and stop
  there. Read one line too many and you will swallow the contestant's opening move; read one
  too few and you will mistake leftover input for it.
- **Flush after every message.** Both programs are blocked on each other's output. A validator
  that leaves its prompt sitting in a buffered stdout will deadlock against a contestant
  waiting to read it, and the attempt will die on the watchdog. Use `flush=True` (Python),
  `fflush(stdout)` / `endl` (C/C++), `System.out.flush()` (Java), etc.
- **Handle a contestant that stops talking.** If the contestant crashes or closes its output,
  the validator's stdin reaches EOF. Exit cleanly with a verdict code (typically `1` / WA)
  rather than crashing — a validator crash is treated as a judge failure, not a contestant
  mistake.

Example (Python, a binary-guessing game). The test case supplies the secret and the query
budget, so `001.in` might hold `42 50` and a harder `002.in` might hold `999999 20`:

```python
import sys

# The test case comes first, on one line: the secret and the query budget.
secret, budget = (int(value) for value in sys.stdin.readline().split())

for _ in range(budget):
    line = sys.stdin.readline()        # now the contestant is talking
    if not line:                       # contestant went away
        sys.exit(1)                    # WA
    guess = int(line)
    if guess == secret:
        sys.exit(0)                    # AC
    print("<" if guess > secret else ">", flush=True)
sys.exit(2)                            # too many queries -> TLE
```

Each case runs this program from the top on a fresh process, so there is no state to reset
between rounds. The judge stops at the first case that does not exit `0`.

## 5. Exit codes and verdicts

The validator exits once **per test case**, and that exit is that case's verdict. A **clean
exit** (it terminated normally, not by a signal) maps as follows:

| Validator exit code | Verdict | Meaning |
| --- | --- | --- |
| `0` | `AC` | Accepted |
| `1` | `WA` | Wrong Answer |
| `2` | `TLE` | Time Limit Exceeded — see the note below |
| `4` | `PE` | Presentation Error (malformed but otherwise plausible output) |
| any other clean exit | `RE` | Treated as a **contestant** Runtime Error |

Note that an *undocumented* clean exit code (`3`, `5`, `127`, …) is not a judge error: it is
reported as contestant `RE`. Only exit codes `0`, `1`, `2`, `4` carry meaning.

Terminating by a **signal** (segfault, uncaught exception in some runtimes, OOM-kill of the
validator itself) is *not* a verdict — it is an internal judge failure, and is retried
(see [Failure handling](#7-failure-handling-and-retries)).

### Verdict precedence

Within one test case, judge-enforced contestant limits are checked first. After that, a clean
validator exit is the verdict, unless the contestant *crashed* — which is what "broken protocol"
actually means.

| Situation | Validator outcome | Contestant outcome | Verdict |
| --- | --- | --- | --- |
| Contestant memory limit reached | any | OOM-killed by isolate | `MLE` |
| Contestant output limit reached | any | output byte limit reached | `OLE` |
| Both complete cleanly | clean exit `0` | clean exit `0` | `AC` |
| Validator exits first | clean exit `0` | exits later or receives EOF | `AC` |
| Validator exits first | clean exit `1` | exits later or receives EOF | `WA` |
| Validator exits first | clean exit `2` | exits later or receives EOF | `TLE` |
| Validator exits first | clean exit `4` | exits later or receives EOF | `PE` |
| Validator exits first | other clean exit | exits later or receives EOF | `RE` |
| Contestant exits `0` first | any clean exit | clean exit `0` | the validator's verdict |
| Contestant crashes | any | nonzero exit or killed by a signal | `RE` |
| Contestant exits first, validator never exits cleanly | no clean validator exit | any | `RE` |
| Validator crashes | signal, startup failure, or communication failure | any | internal failure, retried once |
| Validator never exits cleanly | no clean validator exit, and the contestant did not exit first | any | internal failure, retried once |
| Neither side finishes before the watchdog | no clean validator exit | any | internal failure, retried once |

A clean validator exit is a verdict signal, and it is authoritative whichever side the judge
observes terminating first. A contestant that prints its final answer and exits `0` is *not*
breaking the protocol: it has simply finished, and its exit races the validator's own, so
deciding on the observed order would make a correct solution pass or fail at random. The
contestant is only charged `RE` when it actually crashed (nonzero exit or a signal), or when it
ended first and left the validator with no clean verdict at all.

Your solution therefore does **not** need to linger after its final answer — printing the answer
and exiting immediately is fine.

The submission's verdict is then simply the verdict of the **first case that was not `AC`**, or
`AC` when every case passed. Its reported wall time and memory are the worst readings across
the cases that actually ran.

## 6. Which limits are enforced, and by whom

This is the part that most often surprises people: **for interactive problems, the time limit
is the validator's responsibility.**

### Enforced by the judge (isolate / Docker), on the contestant

| Limit | Enforced by | Effect |
| --- | --- | --- |
| Problem **memory limit** | isolate `--cg-mem` | Contestant OOM-kill → `MLE` |
| Problem **PIDs limit** | isolate `--processes` | Fork bomb / excess processes killed |
| Problem **output limit** (bytes of contestant stdout) | the judge's own byte counter on the relayed stream | Both processes killed → `OLE` |
| Network | Docker `network_mode` (`none` in production) | No network access |
| Container memory / PIDs backstop | Docker `mem_limit` / `pids_limit` (`NOCA_JUDGE_CONTAINER_MEM_LIMIT_MB`, `NOCA_JUDGE_CONTAINER_PID_LIMIT`) | Hard ceiling above the problem limits |

### **Not** enforced by the judge

- **The problem's CPU and wall-time limits are *not* applied** to an interactive run. The
  contestant runs under isolate without `--time` / `--wall-time`, because the contestant
  legitimately spends most of its wall-clock time *blocked* waiting for the validator, and a
  naive wall clock would punish a correct solution for the validator's own slowness.
  **If your problem needs a time or query budget, the validator must enforce it** — count
  queries, measure elapsed time yourself, and exit `2` (`TLE`) when the contestant exceeds it.
  The example in §3 does exactly this with a query cap.

### Limit metadata available to the validator

The validator process receives the submitted language and the effective problem
limits as environment variables. These values let you adapt the protocol or
enforce a custom time budget from inside the validator; they do not change which
limits the judge enforces automatically.

| Environment variable | Value |
| --- | --- |
| `PROBLEM_TIME_LIMIT` | Effective time limit for the submitted language, in milliseconds |
| `PROBLEM_OUTPUT_LIMIT` | Effective contestant-output limit, in bytes |
| `PROBLEM_MEMORY_LIMIT` | Effective memory limit for the submitted language, in KiB |
| `PROBLEM_PID_LIMIT` | Effective PID limit for the submitted language |
| `USER_LANGUAGE` | Submitted language ID, such as `python3` |

For Web contest problems, validators also receive `PER_LANGUAGE_LIMITS`: a JSON
object keyed by language ID. Each entry contains the effective limits that would
apply to that language:

```json
{
  "python3": {
    "time_limit_ms": 1000,
    "memory_limit_kb": 262144,
    "pids_limit": 64,
    "output_limit_in_bytes": 65536,
    "repetitions": 1
  }
}
```

Arena problems do not set `PER_LANGUAGE_LIMITS` because Arena has one effective
limit set per problem, not contest-level per-language overrides.

### Applied to the validator

The validator is **trusted code** (you wrote it, not the contestant), and it runs *without* the
problem's resource limits: no `--cg-mem`, no problem PID limit, no time limit. It is still
confined to its own network-disabled container under isolate, with the Docker-level container
memory and PID ceilings as a backstop.

### The watchdog

An emergency wall-clock watchdog covers **each test case's attempt**:
`NOCA_JUDGE_CUSTOM_VALIDATOR_WATCHDOG_SECONDS` (default **300 s**, applied per case, not to the
submission as a whole). This is a protection against a stalled protocol (e.g. both sides waiting
on each other because someone forgot to flush) — **it is not the problem's time limit**, and a
submission that hits it is reported as an internal judge failure, not as contestant `TLE`.

## 7. Failure handling and retries

An attempt that ends without a clean validator exit — validator signal, container
startup/communication failure, or watchdog expiry — is an **internal failure**, not a
contestant verdict. That **test case** is retried **once**, on two brand-new containers (an
unclean exit leaves both containers killed, so the retry cannot reuse them). A case that then
passes lets the judgment carry on to the next case as normal.

If the second attempt at a case also fails to produce a clean exit, the validator is presumed
broken:

- **Contest:** the judgment is marked `FAILED` with an explanatory message for staff.
- **Arena:** *crash containment* fires. The active revision is marked `RUNTIME_FAILED`, the
  problem is **disabled**, other queued judgments for that problem are failed and dequeued,
  and the problem's owner receives a notification. Fix the validator and upload a new revision
  to bring the problem back.

Things that never count as a validator crash: a clean but undocumented exit code, a contestant
crash, and a contestant that hits `MLE` / `OLE`. A validator that **fails to compile** at
submission time is an internal failure too (`FAILED`, not `CE` — a `CE` would wrongly blame the
contestant).

## 8. Diagnostics

Each attempt is recorded in `submission_interactive_attempts` /
`arena_submission_interactive_attempts`, holding the `test_case_ordinal` it belongs to, both
exit codes and signals, the contestant's wall time and memory, the enforced-limit outcome,
either the clean validator verdict or a typed crash reason, bounded **stderr** excerpts for both
sides, and the **transcript**.

A judgment retains only the attempts of the **last executed test case** (at most two rows: the
attempt plus its retry). Starting a new case clears the previous case's rows, so what survives
is the conversation that actually decided the submission — the failing one. On an accepted
submission every case passed, so there is nothing to explain and **the frontends show no
transcript at all**; they render it only for a non-`AC` verdict, naming the test case it came
from.

The transcript is the conversation itself, captured by the judge as it relays the bytes:
an ordered, line-split JSON list of `{"dir": "user" | "validator", "line": ...}` entries. The
test case's own input is **not** part of it — it is the problem's data, not something either
side said. Recording is capture-only: past its 256 KiB cap the transcript is flagged truncated
and stops growing, while the run itself continues unaffected — a verdict never depends on the
recording.

Diagnostics (including the validator source) are visible only to the audiences already trusted
with test-case data: contest admins/judges and the submission's owner in Arena. They are never
shown to other contestants.

## 9. Limits and constants at a glance

| Thing | Value |
| --- | --- |
| Validator source upload | UTF-8, max **256 KiB**, non-empty |
| Compiler diagnostics retained on a failed candidate | 16 384 characters |
| Test cases required | **at least 1 secret** case; an interactive problem has no public ones |
| Sample interactions per problem | max **5** (`MAX_SAMPLE_INTERACTIONS`) |
| Attempts per test case | 2 (one retry, only for unclean validator exits) |
| Attempt rows kept per judgment | 2 (the last executed case only) |
| Interactive watchdog | `NOCA_JUDGE_CUSTOM_VALIDATOR_WATCHDOG_SECONDS`, default **300 s**, **per test case** |
| Contestant output limit | the problem's `output_limit_in_bytes`, else `NOCA_JUDGE_OUTPUT_LIMIT_BYTES` (default 64 MB), counted per test case |
| Validator limit env vars | `PROBLEM_TIME_LIMIT`, `PROBLEM_OUTPUT_LIMIT`, `PROBLEM_MEMORY_LIMIT`, `PROBLEM_PID_LIMIT`, `USER_LANGUAGE`; Web contest problems also set `PER_LANGUAGE_LIMITS` |
| Transcript recording cap | 256 KiB per attempt, then flagged truncated |
| stderr excerpt cap (per side) | 16 KiB |
