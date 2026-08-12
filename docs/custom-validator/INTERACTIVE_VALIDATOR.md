# Custom interactive validators

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

- [Default token validator](TOKEN_VALIDATOR.md) — the built-in comparison used
  by standard, non-interactive problems
- [Submission data flow](../DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md) — where
  the interactive branch sits in the submission lifecycle
- [Autojudge infrastructure](../../autojudge/docs/AUTOJUDGE_INFRA.md) — worker
  isolation and the queue protocol
- [Configuration](../CONFIG.md) —
  `NOCA_JUDGE_CUSTOM_VALIDATOR_WATCHDOG_SECONDS` and the container limits
- [Output checker validator rationale](OUTPUT_CHECKER_VALIDATOR.md) — the
  rationale for a separate, non-interactive custom validation strategy

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
- Every test case is **secret**. Interactive problems publish separate sample
  interactions instead of test-case inputs or expected outputs.
- No submission `test_results` rows are produced. The per-attempt record is an
  interactive-attempt row instead (see [Diagnostics](#8-diagnostics)).

## 2. Setting a validator on a problem

The validator is a single **UTF-8 source file** in any globally active
Autojudge language, not necessarily a language enabled for the contest. NOCA
compiles it with that language's normal compile command.

The source must be non-empty and non-blank, and its encoded upload must not
exceed 256 KiB. Multi-file bundles, prebuilt binaries, archives, and
problem-defined build commands are not supported. NOCA uses the selected
language's registered source filename, compile image, compile command, compile
timeout, artifact rules, run image, and run command. The registered run command
is the validator entry point; the problem cannot override it.

NOCA does not supply or inject a validator helper library. A validator must be
a standalone program that uses its language's normal runtime facilities and
this protocol. The reference implementations later in this guide are examples,
not runtime dependencies.

The validation strategy is selected when the problem is created and is
immutable afterward. An interactive problem cannot become a standard or checker
problem; changing strategy requires creating a new problem from scratch. Web and
Arena must eventually enforce this invariant in their creation and edit
workflows. Removing validator source leaves the problem interactive but unable
to accept submissions until another validator candidate becomes active.

NOCA stores the language, UTF-8 source, and revision metadata, but never retains
a compiled validator artifact. Candidate validation discards its artifact. The
active source is compiled once for every submission or solution-test judgment,
and that judgment-local artifact may be reused across its test cases. No
artifact is cached across judgments or revisions.

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
- **Compiler rejection or compilation-limit violation** → the candidate is kept
  as `INVALID`, with bounded diagnostics shown on the page. An older active
  revision, if any, keeps working.
- **Infrastructure failure** → the candidate remains `PENDING` while queue
  recovery retries validation. The previous active revision, if any, keeps
  working.

Here, **validated** means compilation or syntax checking only. Candidate
validation never starts the validator, uses no problem test case or sample
interaction, and accepts no author-provided valid or invalid protocol vectors.
It therefore cannot prove that the compiled validator follows the runtime
protocol; runtime crashes and other protocol outcomes follow
[Failure handling and retries](#7-failure-handling-and-retries) when an actual
judgment runs.

Candidate compilation uses a disposable network-disabled compile container,
the selected language's registered compile timeout, a 512 MB container memory
limit, and a 128-PID container limit. Retained compiler diagnostics are capped
at 8,192 characters by the shared compiler. There is no interactive watchdog
or protocol-stream limit during candidate validation because the validator is
not run.

Promotion is token-fenced and transactional. The candidate is committed before
its job is enqueued. The worker compiles it only while the candidate token still
matches and its state is `PENDING`; a stale or superseded result is a no-op.
Successful promotion updates the active language, source, state, and validation
timestamp and clears the candidate fields in one commit. A compiler rejection
or compilation-limit violation changes only the matching candidate to `INVALID`
and preserves any active revision.

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
revision nor candidate state changes, and queue recovery may retry the job.

When a problem has no active `VALID` validator, submissions to it are refused,
and an Arena problem cannot be enabled. A pending or invalid replacement does
not block judging with the previous active `VALID` revision.

**Replacing a validator:** upload the new source as a candidate without removing
the active revision. Both domains continue judging with the previous active
`VALID` revision while the replacement is `PENDING` or `INVALID`. A successful
candidate promotion atomically replaces the active revision. **Removing** a
validator clears both the active and candidate revisions; any in-flight
validation job for the removed candidate is discarded harmlessly because its
token no longer matches. Removal does not convert the problem to another
validation strategy.

Removing a validator also leaves the problem's [sample interactions](#3-test-cases-and-sample-interactions)
with nothing to illustrate, so the removal is confirmed through a modal that makes you choose:
**keep** them (they are hidden, and resurface if you add a validator again) or **delete** them
permanently. The endpoints require that choice as an exact `keep_interactions=true|false`; there
is no default.

**Downloading:** the problem edit page offers the current active validator
source. The Web Contest submission review page also offers that current source
to authorized staff and labels it as current. Arena submission detail pages do
not display a validator source or version. NOCA does not retain the validator
revision that originally judged a completed submission, and no submission page
can reconstruct it. Replacing or removing the active validator may therefore
change or remove the source available from a historical Web submission page.

Changing a validator does not change completed judgment records automatically.
An administrator who wants existing submissions evaluated with the replacement
must explicitly requeue those submissions; the new judgments use the active
validator at dispatch time. Persistently pinning each judgment to its validator
revision may be added in a future release.

**Profiling and Auto-Limit:** interactive problems support neither profiling nor
Auto-Limit. Their limits must be configured explicitly. Candidate compilation
still uses the profiling-priority queue; that queue placement does not make the
problem eligible for profiling.

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
[problem package format](../PROBLEM_PACKAGE_FORMAT.md).

The future package version that introduces output checkers also makes
`validator_type` explicit. In that version, an interactive package must declare
`"validator_type": "interactive"` together with its `custom_validator`, and
package updates must not change an existing problem's strategy. The current
package format remains unchanged until that version bump is implemented.

## 3. Test cases and sample interactions

An interactive problem's test cases work differently from a plain problem's, and its public
examples are a different thing entirely.

### Test cases: input only, and always secret

Each case carries **input and an optional explanation, no expected output**. The input is written
to the validator's stdin before the conversation starts, so it *parametrizes* one round rather
than declaring an answer; the validator decides the verdict.

Every case is **secret**. A bare input reveals a secret without showing the
contestant what to do with it, so there is nothing worth publishing. Every
problem whose immutable strategy is `interactive` must therefore satisfy this
rule, even while it has no active validator source:

> **zero public test cases, and at least one secret one.**

The application keeps it that way from several directions: selecting the
interactive strategy during creation makes every case secret, the sample/secret
toggle is refused for interactive problems, new cases are forced secret, and no
edit path may remove the last secret case.

The "at least one secret case" half is a **gate**, not a write barrier. Arena's create form
legitimately stages a validator on a brand-new *disabled* problem that has no cases yet, so an
incomplete draft is allowed to exist. It is only forced to be complete where it would become
visible or judgeable: the Arena enable gate, and the submission preflight in both modules.

### Test-case changes and rejudging

Adding, removing, reordering, or replacing an interactive test-case input does
not alter completed judgments, invalidate the active validator, disable the
problem, or automatically requeue submissions. An administrator or problem
owner who wants all existing submissions evaluated against the changed cases
must explicitly request a full rejudgment for that problem. The rejudgment uses
the active validator and test-case data available at its dispatch time.

A queued judgment that has not been dispatched uses the latest published case
set when it is dispatched. At dispatch, Autojudge loads one coherent, ordered,
job-local copy of every test-case input. A running judgment continues to use
that copy, so a concurrent edit cannot make one judgment mix old and new test
cases. The copy is ephemeral and is destroyed when the job ends; completed
judgments do not retain an immutable test-case revision or complete input
snapshot.

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
3. **Read limits from the environment when needed.** `PROBLEM_TIME_LIMIT`,
   `USER_LANGUAGE`, and the other core values listed in
   [Limit metadata available to the validator](#limit-metadata-available-to-the-validator)
   are guaranteed by the runtime contract. They let you scale a query budget to
   the submitted language instead of hard-coding one. A validator may still use
   defensive fallbacks so it can run in a local development environment that
   does not reproduce NOCA's complete contract.
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
limits = read guaranteed environment values when needed

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
| Watchdog after a complete validator line | no clean validator exit | does not send a complete reply | `TLE` |
| Watchdog after a complete contestant line | does not send a complete reply | still running | internal failure, retried once |
| Watchdog before any complete line | no attributable stalled side | any | internal failure, retried once |

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

The validator process always receives the submitted language and the effective
problem limits as the five core environment variables below. Their presence is
part of the runtime contract. These values let you adapt the protocol or enforce
a custom time budget from inside the validator; they do not change which limits
the judge enforces automatically. Defensive handling of missing values is only
portability advice for running a validator outside NOCA.

| Environment variable | Value |
| --- | --- |
| `PROBLEM_TIME_LIMIT` | Effective time limit for the submitted language, in milliseconds |
| `PROBLEM_OUTPUT_LIMIT` | Effective contestant-output limit, in bytes |
| `PROBLEM_MEMORY_LIMIT` | Effective memory limit for the submitted language, in KiB |
| `PROBLEM_PID_LIMIT` | Effective PID limit for the submitted language |
| `USER_LANGUAGE` | Submitted language ID, such as `python3` |

These five values contain everything the validator receives about problem
limits. Web and Arena do not inject the limits of other languages. A validator's
behavior must not depend on which unrelated languages are enabled or on their
configured limits.

### Applied to the validator

The validator is **trusted code** (you wrote it, not the contestant), and it runs *without* the
problem's resource limits: no `--cg-mem`, no problem PID limit, no time limit. It is still
confined to its own network-disabled container under isolate, with the Docker-level container
memory and PID ceilings as a backstop.

### The watchdog

An emergency wall-clock watchdog covers **each test case's attempt**:
`NOCA_JUDGE_CUSTOM_VALIDATOR_WATCHDOG_SECONDS` (default **300 s**, applied
per case, not to the submission as a whole). This protects against a stalled
line protocol, such as both sides waiting on each other because one side didn't
send or flush its next line. It is **not** the problem's CPU or wall-time limit.

When neither process has finished, the bridge uses the last complete relayed
line to attribute the stall. If the validator sent that line, the contestant
failed to reply and receives `TLE`; the judge doesn't retry the case. If the
contestant sent that line, the validator failed to reply, so the judge treats
the watchdog as an internal failure and retries once. A partial line isn't
enough to blame its receiver, and a watchdog before any complete line remains
an ambiguous internal failure.

## 7. Failure handling and retries

An attempt that ends with a validator signal, container startup or communication
failure, or a watchdog attributed to the validator is an **internal failure**,
not a contestant verdict. An ambiguous watchdog is also internal. That **test
case** is retried **once**, on two brand-new containers (an unclean exit leaves
both containers killed, so the retry cannot reuse them). A case that then passes
lets the judgment carry on to the next case as normal. A watchdog attributed to
the contestant is `TLE`, so it doesn't retry and never activates validator
crash containment.

If the second attempt at a case also fails to produce a clean exit, the validator is presumed
broken:

- **Contest:** the judgment is marked `FAILED` with an explanatory message for staff.
- **Arena:** *crash containment* fires. The active revision is marked `RUNTIME_FAILED`, the
  problem is **disabled**, other queued judgments for that problem are failed and dequeued,
  and the problem's owner receives a notification. Fix the validator and upload a new revision
  to bring the problem back.

Things that never count as a validator crash: a clean but undocumented exit
code, a contestant crash, a contestant that hits `MLE` / `OLE`, and a watchdog
attributed to a contestant that didn't reply. A validator that **fails to
compile** at submission time is an internal failure too (`FAILED`, not `CE` — a
`CE` would wrongly blame the contestant).

## 8. Diagnostics

Interactive diagnostics preserve the last conversation that could explain a
judgment without retaining every secret interaction. Attempt records, captured
streams, and validator-source access remain separate concerns.

### Submission-attempt records

Each attempt is recorded in `submission_interactive_attempts` or
`arena_submission_interactive_attempts`. Its row holds the test-case ordinal,
attempt number, both exit codes and signals, contestant wall time and memory,
contestant output byte count, enforced-limit outcome, clean validator verdict
or typed crash reason, bounded stderr excerpts for both sides, and the
transcript.

A judgment retains only the attempts of the last executed test case, with at
most two rows. Recording attempt 1 for a new case clears the previous case's
rows. Recording attempt 2 replaces only an existing attempt-2 row. A crash on
attempt 1 followed by a valid retry therefore leaves both rows only while that
case remains the last executed case.

On an accepted submission every case passed. The final case's attempt rows may
remain in the database, but the frontends show no transcript for `AC`. They
render attempts only for a non-`AC` or failed judgment and identify the test
case that produced them.

### Transcript and stderr capture

The transcript is the conversation captured while the judge relays it. It is
an ordered, line-split JSON list of
`{"dir": "user" | "validator", "line": ..., "partial"?: true}` entries. A
trailing line without a newline carries `partial: true`. The test-case input is
not part of the transcript because it is problem data rather than a message
produced by either process.

The transcript retains a prefix of complete protocol lines containing at most
256 KiB of raw line-content bytes across both directions. Newline separators do
not count toward the cap. If the next complete line would exceed the cap, NOCA
omits that whole line, sets `truncated: true`, and records no later lines. The UI
renders a truncation notice from that flag. Recording is capture-only: the judge
continues relaying bytes, and the cap cannot change the verdict.

Contestant and validator stderr each retain their first 16 KiB of raw bytes per
attempt. NOCA appends no marker and stores no stderr truncation flag. A stored
excerpt of exactly 16 KiB therefore does not prove that the process produced no
additional stderr.

Byte caps apply before decoding. NOCA decodes transcript lines and stderr as
UTF-8 with replacement, so invalid sequences become `U+FFFD`, then removes NUL
characters because PostgreSQL text columns cannot store them.

### Solution-test attempts

Interactive solution tests use `solution_test_case_results`, with a non-null
`attempt_number` distinguishing interactive attempts from ordinary case rows.
They follow the same last-case-only, two-attempt retention policy and the same
transcript and stderr capture limits as submission judgments.

The aligned contract requires solution-test attempts to retain both process
exit codes and signals, both stderr excerpts, the transcript, contestant
measurements, and the clean validator verdict or typed crash reason. The current
schema retains only part of that validator-side detail; completing parity is an
implementation backlog item. Solution-test diagnostics are visible only to the
Contest administrators and judges authorized to access that solution-test run.

### Visibility and validator source

Web Contest submission diagnostics are visible only to Uberadmins, contest
admins, and judges. Contestants cannot access the submission-review page, even
for their own submissions.

Arena diagnostics are visible to the submission owner, Arena admins, and an
authorized teacher viewing the submission through its class-report context.
Other Arena users and unrelated contestants cannot access them. Operators can
inspect persisted attempts and service logs through operational access; this
contract does not introduce an operator-facing application role or page.

Validator-source access is separate. Authorized problem managers can download
the current active source from the problem editor. Authorized Web Contest staff
can also download that current source from a historical submission page, where
it must be labeled as current. Arena submission pages expose no validator source
or version. No submission page can recover the validator revision that produced
a historical judgment.

## 9. Limits and constants at a glance

| Thing | Value |
| --- | --- |
| Validator source upload | UTF-8, max **256 KiB**, non-empty |
| Candidate validation | Source/metadata validation plus compilation or syntax check only; the validator is not run |
| Candidate compiler failure | `INVALID`; retain bounded compiler diagnostics and preserve any active revision |
| Candidate infrastructure failure | Keep `PENDING` and retry through queue recovery; after exhaustion, mark `INVALID` with an internal-failure diagnostic |
| Candidate compile container | Network disabled, 512 MB memory limit, 128-PID limit, registered per-language compile timeout |
| Compiler diagnostics retained on a failed candidate | 8 192 characters |
| Profiling and Auto-Limit | Not supported; configure interactive problem limits explicitly |
| Compiled validator retention | None; compile the active source once per judgment |
| Test cases required | **at least 1 secret** case; an interactive problem has no public ones |
| Sample interactions per problem | max **5** (`MAX_SAMPLE_INTERACTIONS`) |
| Attempts per test case | 2 (one retry, only for unclean validator exits) |
| Attempt rows kept per judgment | 2 (the last executed case only) |
| Interactive watchdog | `NOCA_JUDGE_CUSTOM_VALIDATOR_WATCHDOG_SECONDS`, default **300 s**, **per test case** |
| Contestant output limit | `min(` the problem's `output_limit_in_bytes`, `NOCA_JUDGE_OUTPUT_LIMIT_BYTES)` (global ceiling, default 64 MB), counted per test case. The problem always states one — the column is NOT NULL — so the global value is a ceiling, not a fallback. |
| Validator limit env vars | `PROBLEM_TIME_LIMIT`, `PROBLEM_OUTPUT_LIMIT`, `PROBLEM_MEMORY_LIMIT`, `PROBLEM_PID_LIMIT`, `USER_LANGUAGE` |
| Transcript recording cap | 256 KiB per attempt, then flagged truncated |
| stderr excerpt cap (per side) | 16 KiB |
