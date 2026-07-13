# Custom Interactive Validators

A custom validator turns a problem into an **interactive** problem: instead of comparing the
contestant's output against a fixed expected-output file, NOCA runs a program you supply — the
validator — and lets the two processes talk to each other. The validator decides the verdict
and reports it through its **process exit code**.

Both problem domains support this: Contest problems (web module) and Arena problems.

Related references:
- [DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md](DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md) — where the interactive branch sits in the submission lifecycle
- [../autojudge/docs/AUTOJUDGE_INFRA.md](../autojudge/docs/AUTOJUDGE_INFRA.md) — worker isolation and the queue protocol
- [CONFIG.md](CONFIG.md) — `NOCA_JUDGE_CUSTOM_VALIDATOR_WATCHDOG_SECONDS` and the container limits

## Table of Contents

- [1. How judging works](#1-how-judging-works)
- [2. Setting a validator on a problem](#2-setting-a-validator-on-a-problem)
- [3. Writing a validator](#3-writing-a-validator)
- [4. Exit codes and verdicts](#4-exit-codes-and-verdicts)
- [5. Which limits are enforced, and by whom](#5-which-limits-are-enforced-and-by-whom)
- [6. Failure handling and retries](#6-failure-handling-and-retries)
- [7. Diagnostics](#7-diagnostics)
- [8. Limits and constants at a glance](#8-limits-and-constants-at-a-glance)

## 1. How judging works

For each submission to a validator problem, the Autojudge:

1. Loads the problem's **active** validator revision and compiles it. The validator is
   recompiled for every submission; binaries are never cached.
2. Compiles the contestant's source. (A contestant compile failure is still a normal `CE`.)
3. Acquires **two** fresh, network-disabled run containers — one for the contestant, one for
   the validator — and wires them together: contestant stdout → validator stdin, validator
   stdout → contestant stdin. EOF (half-close) propagates in both directions.
4. Runs the conversation to completion, then reads the validator's exit code and turns it
   into the verdict.

Interactive judging **never reads test-case input or expected-output files**. The whole
protocol is generated live by the validator. Consequences:

- A validator problem may legitimately have **no test cases at all**.
- Any test cases it does have must be **public samples** (they are only illustrative material
  for the statement; the judge does not use them). The UIs enforce this: you cannot add a
  secret case to a validator problem, nor make an existing one secret.
- No `test_results` rows are produced. The per-attempt record is an
  interactive-attempt row instead (see [Diagnostics](#7-diagnostics)).

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

**Downloading:** the problem edit page offers the current validator source; the submission
review/detail pages offer the validator that judged that submission.

**Problem packages:** an export of a validator problem carries `validator/source.txt` plus a
`custom_validator: {language_id, source_file}` block and `test_case_visibility: "sample"` in
`problem.json`. Importing such a package stages the validator as a fresh candidate (it is
compiled and validated on import, exactly like an upload) and marks every packaged test case
as a sample. Public exports never include validator source.

## 3. Writing a validator

The validator is an ordinary program. It:

- reads the contestant's messages from **stdin**,
- writes its messages to the contestant on **stdout**,
- may write anything it likes to **stderr** — this is never shown to the contestant, only
  retained as a diagnostic for staff,
- ends by **exiting with the code that expresses the verdict** (next section).

Two practical rules:

- **Flush after every message.** Both programs are blocked on each other's output. A validator
  that leaves its prompt sitting in a buffered stdout will deadlock against a contestant
  waiting to read it, and the attempt will die on the watchdog. Use `flush=True` (Python),
  `fflush(stdout)` / `endl` (C/C++), `System.out.flush()` (Java), etc.
- **Handle a contestant that stops talking.** If the contestant crashes or closes its output,
  the validator's stdin reaches EOF. Exit cleanly with a verdict code (typically `1` / WA)
  rather than crashing — a validator crash is treated as a judge failure, not a contestant
  mistake.

Example (Python, a binary-guessing game):

```python
import sys

secret = 42
for _ in range(50):
    line = sys.stdin.readline()
    if not line:                       # contestant went away
        sys.exit(1)                    # WA
    guess = int(line)
    if guess == secret:
        sys.exit(0)                    # AC
    print("<" if guess > secret else ">", flush=True)
sys.exit(2)                            # too many queries -> TLE
```

## 4. Exit codes and verdicts

A **clean exit** of the validator (it terminated normally, not by a signal) maps as follows:

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
(see [Failure handling](#6-failure-handling-and-retries)).

### Verdict precedence

The exit-code map is only consulted last. The full order is:

1. **`MLE`** — the contestant was OOM-killed by isolate (judge-enforced).
2. **`OLE`** — the contestant exceeded the output limit (judge-enforced).
3. **Unclean validator exit** (signal, startup/communication failure, or watchdog) → internal
   failure, retried once.
4. **Contestant `RE`** — the contestant died by a signal or exited non-zero. This wins over the
   validator's verdict: if the contestant crashed, the answer is `RE` even if the validator
   happened to exit `0`.
5. **Validator exit code** → the table above.

## 5. Which limits are enforced, and by whom

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

### Applied to the validator

The validator is **trusted code** (you wrote it, not the contestant), and it runs *without* the
problem's resource limits: no `--cg-mem`, no problem PID limit, no time limit. It is still
confined to its own network-disabled container under isolate, with the Docker-level container
memory and PID ceilings as a backstop.

### The watchdog

A single emergency wall-clock watchdog covers one complete interactive attempt:
`NOCA_JUDGE_CUSTOM_VALIDATOR_WATCHDOG_SECONDS` (default **300 s**). This is a protection
against a stalled protocol (e.g. both sides waiting on each other because someone forgot to
flush) — **it is not the problem's time limit**, and a submission that hits it is reported as
an internal judge failure, not as contestant `TLE`.

## 6. Failure handling and retries

An attempt that ends without a clean validator exit — validator signal, container
startup/communication failure, or watchdog expiry — is an **internal failure**, not a
contestant verdict. It is retried **once**, on two brand-new containers.

If the second attempt also fails to produce a clean exit, the validator is presumed broken:

- **Contest:** the judgment is marked `FAILED` with an explanatory message for staff.
- **Arena:** *crash containment* fires. The active revision is marked `RUNTIME_FAILED`, the
  problem is **disabled**, other queued judgments for that problem are failed and dequeued,
  and the problem's owner receives a notification. Fix the validator and upload a new revision
  to bring the problem back.

Things that never count as a validator crash: a clean but undocumented exit code, a contestant
crash, and a contestant that hits `MLE` / `OLE`. A validator that **fails to compile** at
submission time is an internal failure too (`FAILED`, not `CE` — a `CE` would wrongly blame the
contestant).

## 7. Diagnostics

Each attempt is recorded in `submission_interactive_attempts` /
`arena_submission_interactive_attempts` (at most two rows per judgment), holding both exit
codes and signals, the contestant's wall time and memory, the enforced-limit outcome, either the
clean validator verdict or a typed crash reason, bounded **stderr** excerpts for both sides, and
the **transcript**.

The transcript is the conversation itself, captured by the judge as it relays the bytes:
an ordered, line-split JSON list of `{"dir": "user" | "validator", "line": ...}` entries. The
Arena submission-detail page and the web submission-review page render it as labeled
User/Validator rows, so staff can read the protocol exactly as it happened. Recording is
capture-only: past its 256 KiB cap the transcript is flagged truncated and stops growing, while
the run itself continues unaffected — a verdict never depends on the recording.

Diagnostics (including the validator source) are visible only to the audiences already trusted
with test-case data: contest admins/judges and the submission's owner in Arena. They are never
shown to other contestants.

## 8. Limits and constants at a glance

| Thing | Value |
| --- | --- |
| Validator source upload | UTF-8, max **256 KiB**, non-empty |
| Compiler diagnostics retained on a failed candidate | 16 384 characters |
| Attempts per submission | 2 (one retry, only for unclean validator exits) |
| Interactive watchdog | `NOCA_JUDGE_CUSTOM_VALIDATOR_WATCHDOG_SECONDS`, default **300 s** |
| Contestant output limit | the problem's `output_limit_in_bytes`, else `NOCA_JUDGE_OUTPUT_LIMIT_BYTES` (default 64 MB) |
| Transcript recording cap | 256 KiB, then flagged truncated |
| stderr excerpt cap (per side) | 16 KiB |
