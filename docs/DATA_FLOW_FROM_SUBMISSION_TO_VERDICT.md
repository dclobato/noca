# Data flow: submission to verdict

This document follows a contest submission from the Web request through
autojudge execution and, when configured, human confirmation. PostgreSQL is the
authoritative store; Valkey coordinates queue work and sends live-update nudges.

## Automated judging phase

Every contest submission follows this phase. A completed autojudge-only
judgment receives a final verdict immediately. A contest that requires human
review retains the autojudge verdict but does not publish a final-verdict event
until confirmation.

```
Team user
    │
    │  POST /c/{slug}/runs/submit
    │  multipart { problem_id, language_id, source_file }
    ▼
Web route + submission service
    │  1. Authenticate the contest-scoped JWT cookie; require TEAM
    │  2. Require the contest to be running
    │  3. Validate the problem and contest-assigned language
    │  4. Validate the source upload:
    │     - non-empty text without NUL bytes
    │     - within the contest's maximum source-file size
    │     - within the rolling submission-rate limit
    │     - not a duplicate for this team, problem, language, and source hash
    │  5. Validate judge inputs:
    │     - configured custom validator has an active VALID revision
    │     - at least one judge test case exists
    │     - non-interactive test cases have expected output
    │  6. INSERT submissions row
    │  7. INSERT submission_judgments row (status = QUEUED)
    │  8. INSERT model-hook and WEB submission-created audit rows
    │  9. COMMIT PostgreSQL transaction
    │  10. HSET judge:job:<judgment_id> {
    │        judgment_id,
    │        contest_id,
    │        submission_id,
    │        is_rejudge,
    │        requeue_count,
    │        job_kind
    │      }
    │  11. LPUSH judgment_id to judge:queue:priority
    │  12. PUBLISH SubmissionEvent on judge:submissions
    ▼
Valkey
    │
    │  Lua RPOP profiling|priority|pending and LPUSH inflight
    │  ZADD dispatch time to judge:queue:inflight:times
    ▼
Judge worker
    │  1. Read job_kind and acquire an attempt-token Valkey lock with SET NX EX
    │  2. Load the authoritative submission payload from PostgreSQL
    │  3. UPDATE status = DISPATCHED and stamp worker_id + attempt_token
    │  4. Load effective limits and any active custom validator
    │  5. Compile in a short-lived compile container
    │     - contestant compile failure produces CE
    │  6. UPDATE status = JUDGING
    │  7. Load test-case bytes from the shared filesystem and IDs from PostgreSQL
    │  8. Acquire a warm or on-demand run container for the language
    │  9. For each test case, including configured repetitions:
    │     - inject input and artifact with put_archive()
    │     - reset isolate box state
    │     - run the program through isolate inside the container
    │     - read isolate meta as the authoritative time and memory result
    │     - enforce the NOCA output cap (OLE precedes RE on fsize hits)
    │     - compare output
    │     - INSERT submission_test_results row under the attempt claim
    │     - stop on the first non-AC verdict
    │  10. Destroy the used run container
    │  11. UPDATE submission_judgments and append audit rows:
    │      - status = DONE
    │      - autojudge_verdict = worker result
    │      - final_verdict = worker result only for autojudge-only contests
    │      - final_verdict = NULL when human review is required
    │  12. For an accepted autojudge-only result, create the balloon task
    │  13. For an autojudge-only result, PUBLISH final VerdictEvent
    │      on judge:results
    │  14. Invalidate the contest scoreboard cache
    │  15. Atomically remove the inflight entry, dispatch timestamp, job hash,
    │      and owned attempt lock
    ▼
If contest.autojudge_only = true
    │
    │  autojudge_verdict == final_verdict immediately
    ▼
Submission is fully resolved
```

The PostgreSQL commit and Valkey enqueue form an intentional dual write. The
startup and periodic reconciler rebuild missing queue state for non-terminal
judgments and place recovered submissions on `judge:queue:pending`. The
stale-job reaper handles abandoned inflight work. Attempt-token fences prevent
an older worker from writing results or cleaning up state owned by a replacement
attempt. New submissions and direct rejudge requests use
`judge:queue:priority`; the pending queue is the conservative recovery target.
Judge-side failures transition the judgment to `FAILED`; they do not become
contestant verdicts or publish final-verdict events.

## Contests requiring human review

Judges and contest administrators can confirm an autojudge result. UberAdmins
can view the review page but cannot create confirmations because confirmation
records reference contest users.

```
If contest.autojudge_only = false
    │
    │  judges, admins, and uberadmins may open the review page
    │     GET /c/{slug}/submissions/{submission_id}/review
    │  page shows:
    │     - source code with syntax highlighting
    │     - active judgment test-case results
    │     - current autojudge/final-verdict confirmation panel
    │     - confirmation controls for eligible judges and admins
    ▼
Human review and confirmation flow
    │  1. Only contest JUDGE and ADMIN users can confirm
    │  2. Autojudge-only contests hide confirmation controls; acquire and
    │     confirm endpoints return 404
    │  3. The active judgment must be DONE
    │  4. With Valkey available, the actor acquires the review lock with
    │     POST /c/{slug}/submissions/{submission_id}/acquire-review
    │  5. Submit the confirmation with
    │     POST /c/{slug}/submissions/{submission_id}/confirm
    │  6. One actor may confirm a judgment at most once
    │  7. A chief-judge or ADMIN confirmation sets final_verdict immediately
    │  8. Otherwise, two non-decisive confirmations must match the
    │     autojudge_verdict
    │  9. Model hooks derive final_verdict and append audit rows
    │  10. When final_verdict becomes available, Web:
    │      - creates an accepted-result balloon task when required
    │      - commits the authoritative result
    │      - publishes a final VerdictEvent on judge:results
    │      - invalidates the contest scoreboard cache
    ▼
Submission is fully resolved
```

## Custom-validator branch

When a problem has a configured validator, submission and dispatch both require
an active `VALID` revision. Autojudge reloads that revision at dispatch,
compiles the validator first, and then compiles the contestant. Validator
compilation or infrastructure failure ends as internal `FAILED`; contestant
compilation still ends as `CE` once a validator artifact exists.

Interactive judging reads test-case **input** but never expected output: the
input parametrizes the validator. One container pair judges the whole
submission, and the judge replays the conversation once per test case in
ordinal order, writing that case's input to the validator's stdin before the two
sides talk. A case that ends `AC` advances to the next; the first non-`AC` case
stops the iteration, and its verdict becomes the submission's, exactly as the
first failing case does on a non-interactive problem. Reported wall time and
memory are the worst readings across the cases that ran. A problem with no test
cases cannot be judged and ends as internal `FAILED`.

Interactive judging creates no test-result rows. It stores one or two
interactive-attempt rows for only the last executed test case, tagged with its
`test_case_ordinal`, carrying both exit codes/signals, contestant
resource usage, enforced-limit outcome (`MLE`, `OLE`, or a contestant-attributed
watchdog `TLE`), either the clean validator verdict or a typed crash reason,
bounded stderr excerpts, and an ordered `transcript` of the conversation.
Starting a new case clears the previous case's rows, so what survives is the
round that decided the submission.

The `transcript` JSON column is the record of the protocol itself:
`{"lines": [{"dir": "user" | "validator", "line": ..., "partial"?: true}],
"truncated": bool}`. The judge relays every byte between the two processes, so
it records both sides in the order it observed them, split into protocol lines
(`partial` marks a trailing line that ended without a newline). The test case's
own input is deliberately excluded — it is the problem's data, not something
either side said. It is null when the bridge never ran, such as after a startup
failure or outer safety timeout. A bridge watchdog retains every line exchanged
before the stall. Recording is capture-only: past a 256 KiB cap the transcript
reports itself `truncated` while the bridge keeps relaying, so capture
truncation never changes a verdict. The Arena submission-detail and web
submission-review pages render it as the User/Validator conversation, and only
for a non-`AC` verdict — an accepted submission has no failing round to explain.

Per case, final precedence is `MLE`, `OLE`, contestant-attributed watchdog
`TLE`, unclean validator failure, contestant `RE`, then validator exit mapping:
`0` to `AC`, `1` to `WA`, `2` to `TLE`, and `4` to `PE`. Every other clean
validator exit is contestant `RE`. On watchdog expiry, the last complete
protocol line identifies the stalled receiver: a validator line followed by no
contestant reply is contestant `TLE`; a contestant line followed by no validator
reply is an internal validator failure. A partial line or no complete line is
ambiguous and remains internal. Only an internal failure retries once, on a
fresh container pair, for that case; a second internal Arena attempt activates
crash containment. Diagnostics are available only through the existing
authorized submission-detail/review pages.

## Key architectural decisions

These decisions preserve immutable submissions, recoverable queue work, and
clear ownership between Web and the judge worker.

### Why separate `submission` from `submission_judgment`?

Submissions are immutable legal records of what the contestant sent. Judgment
is a process that can be repeated through rejudging. Keeping them separate
preserves full rejudge history without changing the original record.

### Why keep `autojudge_verdict` and `final_verdict` separate?

The worker always records what the machine concluded, even when contest policy
requires human review. This preserves the raw autojudge result for audits and
rejudges while the final visible verdict remains unresolved until judges
confirm it.

### Why use `judgment_id` as the queued job identity?

Rejudges create new `submission_judgment` rows for the same immutable
submission. Queueing the judgment rather than the submission lets each judgment
attempt move independently through the worker, audit, and confirmation
pipeline.

### Why use Valkey lists and inflight tracking?

The queue protocol is deliberately small: Valkey lists, inflight timestamps,
per-attempt locks, and Lua transitions for dequeue, reconciliation, stale
recovery, and owned cleanup. This keeps each cross-replica state transition
atomic while leaving the runtime state inspectable with standard Valkey tools.

### Why are Web and the judge separate processes?

The worker executes untrusted code and needs a different security posture from
the `web/` process. Keeping the boundary at PostgreSQL, Valkey, and shared
filesystems makes it possible to harden and scale each side independently.

### Why warm run-container pools but use short-lived compile containers?

Run-phase latency matters on every submission and test case, so warm pools
amortize startup cost even though `isolate` is the authoritative inner judge.
Compilation happens once per judgment and has different write patterns, so a
disposable compile container is simpler and safer.

### Why destroy a run container after one judged submission?

The pool provides warm startup, not reuse across contestants. After contestant
code runs, the container is tainted and is destroyed to prevent state leakage
between submissions.

### Why use overlay-backed `/sandbox` instead of tmpfs?

The Docker file-injection mechanism uses `put_archive()`. A tmpfs mount at
`/sandbox` would shadow the injected files. The per-container overlay filesystem
keeps the workspace ephemeral while remaining compatible with Docker's archive
APIs.

### Why support decisive confirmation or two matching confirmations?

A contest administrator or the chief judge can finalize immediately. Other
judges require two confirmations that match the autojudge verdict. The data
model records the confirmations and the derived final verdict.
