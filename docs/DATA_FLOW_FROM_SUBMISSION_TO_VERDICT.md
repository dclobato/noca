# Data Flow: Submission to Verdict

## Autojudge-only contests
```
Team User
    │
    │  POST /c/{slug}/runs/submit
    │  { contest_id, problem_id, language_id, source_code }
    ▼
Web Router + SubmissionService
    │  1. Authenticate user from bearer token / web session
    │  2. Require role = team
    │  3. Validate:
    │     - team belongs to contest
    │     - contest is active and within submission window
    │     - problem is assigned to contest and not excluded
    │     - language is active
    │     - source_code is non-empty
    │  4. INSERT submissions row
    │  5. INSERT submission_judgments row (status = QUEUED)
    │  6. INSERT submission_judgment_audit row
    │  7. COMMIT PostgreSQL transaction
    │  8. HSET judge:job:<judgment_id> {
    │       judgment_id,
    │       contest_id,
    │       submission_id,
    │       is_rejudge,
    │       requeue_count,
    │       job_kind
    │     }
    │  9. LPUSH queue with judgment_id
    │     - judge:queue:priority when contest is running
    │     - judge:queue:pending otherwise (for rejudges)
    ▼
  Valkey
    │
    │  Lua move profiling|priority|pending -> inflight
    ▼
Judge Worker
    │  1. Acquire Redis idempotency lock for judgment_id
    │  2. Load full submission payload from PostgreSQL
    │  3. UPDATE judgment status = DISPATCHED
    │  4. Compile in short-lived compile container
    │     - on compile failure: verdict = CE
    │  5. UPDATE judgment status = JUDGING
    │  6. Load problem limits and test cases
    │  7. Acquire warm run container from language pool
    │  8. For each test case:
    │     - inject input/artifact with put_archive()
    │     - reset isolate box state
    │     - run program through isolate inside the container
    │     - read isolate meta as the authoritative time/memory result
    │     - enforce NOCA output cap (OLE wins before RE on SIGXFSZ/fsize hits)
    │     - compare output
    │     - INSERT submission_test_results row
    │     - stop on first non-AC verdict
    │  9. Destroy used run container
    │  10. UPDATE submission_judgments:
    │      - status = DONE
    │      - autojudge_verdict = worker result
    │      - final_verdict = autojudge result only when contest.autojudge_only = true
    │      - otherwise final_verdict remains NULL
    │  11. INSERT audit row(s)
    │  12. PUBLISH VerdictEvent on judge:results
    │  13. Remove judgment_id from inflight
    ▼
If contest.autojudge_only = true
    │
    │  autojudge_verdict == final_verdict immediately
    ▼
Submission is fully resolved
```

## Contests requiring human review
```
If contest.autojudge_only = false
    │
    │  judges/admins/uberadmins may open the submission review page
    │     GET /c/{slug}/submissions/{submission_id}/review
    │  page shows:
    │     - source code with syntax highlighting
    │     - active judgment test-case results
    │     - current autojudge/final-verdict confirmation panel
    │     - confirmation form for judges who have not confirmed yet
    ▼
Human review and confirmation flow
    │  1. Only contest judges can confirm
    │  2. Contest is 404 here when `contest.autojudge_only = true`
    │  3. The active judgment must already be `DONE`
    │  4. A judge acquires the review with
    │     POST /c/{slug}/submissions/{submission_id}/acquire-review
    │  5. The confirmation is submitted with
    │     POST /c/{slug}/submissions/{submission_id}/confirm
    │  6. One judge may confirm a given judgment at most once
    │  7. If the confirming judge is the contest chief judge:
    │     final_verdict = that judge's confirmed_verdict immediately
    │  8. Otherwise:
    │     final_verdict is derived by the submission model from stored confirmations
    │     and currently finalizes when two non-chief confirmations match the autojudge verdict
    │  9. Every confirmation and finalization step writes an audit trail through the judgment model hooks
    │  10. If confirmation makes `final_verdict` available, the web layer publishes `VerdictEvent`
    │     and invalidates the contest scoreboard cache
    ▼
Submission is fully resolved
```

## Key Architectural Decisions (Rationale)

**Why separate `submission` from `submission_judgment`?**
Submissions are immutable legal records of what the contestant sent. Judgment is a process that can be repeated (rejudge). Keeping them separate allows full rejudge history without ever touching the original record.

**Why keep `autojudge_verdict` and `final_verdict` separate?**
The worker should always record what the machine concluded, even when contest policy requires human review. This preserves the raw autojudge result for audits and rejudges while allowing the final visible verdict to remain unresolved until judges confirm it.

**Why use `judgment_id` as the queued job identity?**
Rejudges create new `submission_judgment` rows for the same immutable submission. Queueing the judgment rather than the submission lets each judgment attempt move independently through the worker, audit, and confirmation pipeline.

**Why Redis lists + inflight tracking instead of a heavier job framework?**
The queue protocol is deliberately small: `LPUSH`, a Lua ready-job move, an inflight list, a reaper, and a per-judgment Redis lock. That is enough for this workload and keeps failure modes easy to inspect with plain Redis tooling.

**Why are Web and judge separate packages/processes?**
The worker executes untrusted code and needs a very different security posture from the web/ process. Keeping the boundary at PostgreSQL + Redis + shared filesystems makes it possible to harden and scale each side independently.

**Why warm run-container pools but short-lived compile containers?**
Run phase latency matters on every submission and on every test case, so warm pools amortize startup cost even though the authoritative inner judge is now `isolate`. Compile phase happens once per judgment and has different write patterns, so a disposable compile container is simpler and safer there.

**Why destroy a run container after one judged submission instead of returning it to the pool?**
The pool is only for warm startup, not for reuse across contestants. Once contestant code has run, the container is considered tainted and is destroyed to prevent state leakage between submissions.

**Why use overlay-backed `/sandbox` instead of tmpfs for injected files?**
The current Docker file-injection mechanism uses `put_archive()`. A tmpfs mount at `/sandbox` would shadow the injected files. Using the per-container overlay filesystem keeps the workspace ephemeral while remaining compatible with Docker's archive APIs.

**Why support chief-judge immediate finalization or two matching judge confirmations?**
It matches the current human-review workflow: normal review requires agreement between two judges, but a chief judge can break ties and finalize immediately. The data model records both the human confirmations and the final derived verdict.
