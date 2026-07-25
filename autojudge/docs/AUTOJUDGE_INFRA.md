### (Auto)Judge Infrastructure

- The judge worker is a separate async process in `judge/` and does not import from `web/`.
- API and judge communicate only through PostgreSQL, Redis, and the shared test-case filesystem.
- Worker concurrency is fixed-width: one async consumer loop per slot, plus a reaper loop and a reconciler loop.
- Queue protocol:
  - priority queue: `judge:queue:priority`
  - normal queue: `judge:queue:pending`
  - inflight list: `judge:queue:inflight`
  - per-job hash: `judge:job:<job_id>` — the id is a `judgment_id` for web/Arena submissions, a `profiling_run_id`, a `validation_id`, or a `solution_test_run_id`, depending on `job_kind`
    - expected fields for web jobs: `judgment_id`, `contest_id`, `is_rejudge`, `requeue_count`, `job_kind`
    - expected fields for Arena jobs: `judgment_id`, `submission_id`, `user_id`, `problem_id`, `language_id`, `requeue_count`, `job_kind`
    - optional traceability field: `submission_id`
    - rollout compatibility: older hashes may miss `contest_id`; tooling must bucket them as unknown
    - expected fields for solution-test jobs: `solution_test_run_id`, `contest_id`, `problem_id`, `language_id`, `requeue_count`, `job_kind`
    - `job_kind` distinguishes normal `submission`, `arena_submission`, `profiling` (Auto-Limit), `custom_validator_validation`, and `solution_test` jobs
  - result channel: `judge:results`
- Non-scoring solution tests (`job_kind=solution_test`) are staff-triggered runs
  of a candidate solution against a contest problem's real compiler, sandbox,
  limits, test cases, and custom validator. They share the contestant
  priority/pending queues and the same `priority=contest.is_running` rule, and
  their processor takes no Valkey handle at all. Publishing a verdict event,
  invalidating the scoreboard cache, or creating a balloon task is structurally
  impossible rather than test-enforced. Results land in `solution_test_runs` /
  `solution_test_case_results`; ordinary case rows snapshot input, expected
  output, and submission output with a 10 KB cap per value.
- Exhaustion tombstone: when a `solution_test` job exceeds `REAPER_MAX_REQUEUE_COUNT`, the reaper does **not** delete its job hash. It rewrites the hash with `reaper_dropped=true` (the same treatment `custom_validator_validation` gets) and clears the lock and inflight entries. Deleting the hash would make the run look like missing queue state and resurrect it on every pass. The reconciler detects the tombstone inside its atomic decision, marks the run `FAILED` with an explanatory `error_message`, and only then deletes the hash.
- Jobs are intentionally lightweight in Redis. The worker dequeues a bare `job_id`, reads `job_kind` from that job's hash to pick a pipeline, and loads the full payload for that kind from PostgreSQL.
- Arena jobs share the normal pending queue and the same compile/run containers as web
  jobs. Their adapter reads test-case ordinals from Arena database rows and loads the
  content from the shared filesystem at
  `<NOCA_PROBLEM_TESTCASE_DIR>/arena/<problem_id>/NNN.in|out` (web jobs read
  `<root>/contest/<problem_id>`), stores only the first non-AC test result, and does not
  publish `judge:results` events.
- The worker uses a Redis `SET NX` lock keyed on the dequeued `job_id` (`judge:lock:<job_id>`), whatever its kind, to avoid duplicate processing after requeue races.

### Attempt claims: why the lock is not enough

The Valkey lock does not make a run single-writer. The reaper *deletes* it and requeues any
job that has been inflight past `REAPER_STALE_THRESHOLD_MINUTES`
(`_REAP_STALE_JOB_SCRIPT`), which is correct for a dead worker but indistinguishable from
a slow-but-alive one — a submission that TLEs across many cases can legitimately cross the
threshold. From that moment two attempts at the same run execute concurrently, and they
routinely share a `worker_id`: the requeue is usually picked up by the same host, often by
the same process, so `worker_id` cannot tell them apart.

The claim can. Every `set_*_dispatched` accessor stamps an **attempt token** — the
dispatch's Valkey lock token, which is already attempt-scoped (`worker_id:uuid4`), so the
database and the queue agree on who holds the job. It is stored in `attempt_token` on all
four worker-owned run tables: `submission_judgments`, `arena_submission_judgments`,
`profiling_runs`, and `solution_test_runs`. The status fence on dispatch is deliberately
*not* a claim (`DISPATCHED`/`JUDGING`/`RUNNING` are accepted, since taking a run over from
a stalled attempt is the whole point of the requeue); stamping the new token is what
revokes the older attempt's right to write.

Every subsequent write of an attempt is then fenced on the token it stamped:

- `set_*_judging` / `set_*_running` and `set_*_done` update
  `WHERE id = … AND attempt_token = …`, so a stale attempt finishing late cannot overwrite
  the new owner's verdict, append a second terminal audit row, re-notify the user, re-run
  first-solve accounting, or — for profiling — publish its `problem_language_limits` over
  the new owner's. `set_profiling_done` takes the claim *before* writing the limits, in the
  same transaction, so a lost claim leaves nothing behind.
- Result inserts fold the ownership test into the INSERT's source
  (`INSERT … SELECT … WHERE EXISTS (claim)`, `_DatabaseBase._claimed_insert_source`), so no
  window exists between checking the claim and writing.
- `insert_interactive_attempt` and its solution-test counterpart check the claim *before*
  their delete, which would otherwise drop the new owner's transcript.
- `set_*_failed` is fenced on a non-terminal status **and** on a claim that is either this
  attempt's or absent: a run claimed by another attempt is that attempt's to finish, while
  an unclaimed one (the job failed before dispatch could stamp it) is legitimately ours to
  fail.

A lost claim raises `JudgmentOwnershipLost`, which `dispatch_job` treats as a clean abort:
the attempt is logged and counted as `jobs_completed_total{outcome="ownership_lost"}`, and
**no** terminal `FAILED` is persisted — stamping one would bury the verdict the new owner
is about to write. Legacy rows with a NULL token, and callers that pass no token, keep the
previous unfenced behavior.

Two result tables have a unique key of their own
(`arena_submission_test_results.judgment_id`,
`submission_test_results (judgment_id, test_case_id)`); those keep it as a second line of
defence, and `_insert_result_row_once` reconciles a collision from the *same* attempt
rather than failing a judgment that already holds a correct verdict: the replay is accepted
when both writes blamed the same case with the same verdict (re-measured time, memory, exit
status, and output legitimately differ between two executions and are only logged), and
raised only when they disagree on *which* failure was recorded, which no ordering of one
attempt's own work can produce. `profiling_case_results` and `solution_test_case_results`
have no such key — a stale row there would silently interleave with the new owner's rather
than collide — so for them the claim is the only guard, applied through
`_DatabaseBase._insert_claimed_row`.

Custom-validator validation jobs need none of this: they are already idempotent through the
candidate token, which the worker re-checks before promoting or updating a revision.

Profiling and solution-test runs do not carry claims; they are outside the submission
judgment tables and unaffected by this fencing.

- Compile phase:
  - always uses a short-lived compile container
  - interpreted languages still go through the same abstraction, but may only syntax-check and return the source as the artifact
- Language registry:
  - each language row persists profiling defaults used by Auto-Limit runs
  - `profiling_repetitions_default` controls how many times each test case is repeated while profiling that language
  - `profiled_pids_floor` defines the minimum PID limit that can be persisted from profiling for that language
  - profiled repetition counts are copied into `problem_language_limits.repetitions` so judging keeps the exact per-language profiling semantics that produced the stored limits
- Run phase:
  - uses a warm container pool per active language; when `NOCA_JUDGE_PRE_WARM_CONTAINERS=True` each
    language's pool is filled lazily on the first submission for that language (not at startup),
    with concurrent Docker creates across languages capped by `NOCA_JUDGE_WORKER_CONCURRENCY`
  - acquires one idle container, immediately schedules a replacement, and destroys the used container after the run
  - runs contestant programs through `isolate --init/--run/--cleanup` for each executed test case
  - if a problem has no per-language limit row for the submission language, the worker uses the problem fallback resource limits with exactly 1 repetition
  - treats isolate meta output as the authoritative source for time and memory persisted to PostgreSQL
- Current isolation strategy:
  - Docker containers remain the outer safety boundary
  - `network_mode=none` by default
  - generous container-level cgroup memory / PID / CPU limits as a first line of defense
  - `isolate` as the authoritative inner judge for time, memory, process/thread, and stdout-file growth limits
  - run containers are created with `CAP_SYS_ADMIN` and `CAP_NET_ADMIN`, host cgroup namespace, and a read-write bind of `/sys/fs/cgroup` so `isolate` can create namespaces, bring up loopback in the sandbox netns, and manage per-box child cgroups
  - because the host cgroup namespace is shared and `/sys/fs/cgroup` is bind-mounted read-write into every container, an isolate `--box-id=N` resolves to the *same* host cgroup `/sys/fs/cgroup/box-N` across all containers on the host. Each container is therefore assigned a **unique isolate box-id for its entire lifetime** by a process-global allocator (`autojudge/box_registry.py`): allocated at creation (used by the creation preflight), reused for that container's run phase, and released on destruction. This prevents concurrent containers from racing on a shared `box-N` cgroup — the cause of intermittent `Cannot remove control group /sys/fs/cgroup/box-0: Device or resource busy` (EBUSY) / `No such file or directory` (ENOENT) init/cleanup failures that previously hit compile+run-heavy languages (e.g. C++) under load. The id range is `0 .. NOCA_JUDGE_ISOLATE_MAX_BOXES - 1` and assumes one worker process per host.
  - some Ubuntu hosts also require an explicit AppArmor override for run containers; `NOCA_JUDGE_DOCKER_APPARMOR_PROFILE=unconfined` is available when the default profile blocks mount privatization inside `isolate --run`
  - `no-new-privileges` is intentionally not used on isolate-enabled run containers because isolate needs privileged namespace/cgroup setup inside the container
- `/sandbox` lives on the container overlay filesystem, not on a tmpfs mount.
- File injection is done with in-memory tar streams via Docker `put_archive()`.
- Timeout enforcement is layered:
  - compile timeout via `asyncio.wait_for(...)`
  - inner run timeout via `isolate --time/--wall-time`
  - outer run timeout via `asyncio.wait_for(...)` plus container kill if isolate itself hangs
- PID accounting:
  - some isolate builds enforce `--processes` correctly but do not emit peak PID usage in the meta file
  - when isolate meta omits PID usage, the runner falls back to the kernel cgroup file at `/sys/fs/cgroup/box-<id>/pids.peak`
  - profiling therefore uses kernel-observed peak PID counts instead of assuming that missing meta means low PID usage
- Host/runtime prerequisites:
  - isolate must be installed in every run image at `/usr/local/bin/isolate`; the binary is compiled once
    in `noca/isolate-base` and copied into each run image via `COPY --from=isolate` — runtime image-ref
    resolution in `autojudge/image_sync.py` and `shared/language_registry.py` is unchanged
  - run images are standardized on Debian-family or other mainstream glibc-based bases and expose `/lib64`
    as a real path or compatibility symlink so the worker can use deterministic runtime binds
  - the host Docker/cgroup setup must allow a run container with host cgroup namespace and a writable `/sys/fs/cgroup` bind mount to create sub-cgroups
  - worker/container preflight fails fast when isolate or its cgroup init/cleanup prerequisites are unavailable
- Validation scripts:
  - `uv run scripts/autojudge/probe_sandbox_limits.py` validates core sandbox mechanics through the real judge path using small C probes. It is the fast baseline for `network=none`, TLE, stdout OLE, generic file-growth `--fsize`, expected `/tmp` writability inside the sandbox, blocked writes to unmounted paths, read-only system directories, memory, and process-count enforcement.
    - some probes are enforcement assertions: the probe intentionally checks that an operation is blocked, prints `BLOCKED`, exits `0`, and therefore expects `AC`
    - companion probes intentionally do not handle the blocked operation and therefore expect the contestant-visible outcome `RE`
  - `uv run scripts/autojudge/smoke_test_judge.py` validates per-language integration through the same judge path using real sample solutions and test cases. It is the compatibility check for runtime wiring, compile/run images, and language-specific execution details.
- The reaper scans stale inflight jobs and requeues them up to a configured retry limit.
- The reconciler periodically (and at startup) re-scans the database for non-terminal jobs (QUEUED/DISPATCHED/JUDGING) that are missing from the Valkey queue and re-enqueues them. This recovers jobs lost between a producer's DB commit and its follow-up Valkey enqueue (the web/arena submission and rejudge paths commit first, then enqueue) without waiting for a worker restart.

## Recovery boundary: reconciler vs reaper

The two loops recover different failures and must not overlap:

- **The reconciler rebuilds *missing* queue state.** Its discriminator is queue membership, never the database status. An initial snapshot can conservatively skip a known queued job for one pass, but every repair or enqueue reads current membership and mutates it in one Lua script. The script cannot interleave with a worker's atomic dequeue: an inflight or locked job is left alone, an already-queued job is never pushed twice, and only a job absent from every queue and unlocked is rebuilt and enqueued.
- **The reaper reclaims *stale inflight* work.** It is the only loop that requeues a job a worker was executing. Candidate discovery from `judge:queue:inflight:times` is advisory; one Lua transition revalidates the current score and inflight membership before deleting a lock or requeueing. Multiple worker replicas therefore cannot requeue the same stale attempt twice, and an old candidate cannot delete a replacement attempt's fresh lock. When the reconciler finds an inflight job with no timestamp, it writes one without moving an existing score forward.

The invariant that keeps the reaper from reclaiming *live* work is therefore **`REAPER_STALE_THRESHOLD_MINUTES` must exceed the longest legitimate run**. The reaper does not treat the lock as a liveness signal and deliberately deletes it when the timestamp goes stale. The separate `LOCK_TTL_SECONDS > REAPER_STALE_THRESHOLD_MINUTES * 60` assertion keeps the lock from expiring naturally before the reaper's stale decision; it does not protect live work from that decision.

Successful dispatch cleanup is also one Lua transition. Each lock contains an attempt-specific token prefixed by the worker id, and cleanup removes the hash, inflight entry, timestamp, and lock only when that exact token still owns the lock. An expired worker can therefore finish late without deleting a replacement attempt's state.

## Terminal-state fence on dispatch

`set_judgment_dispatched`, `set_arena_judgment_dispatched`, `set_solution_test_dispatched` and `set_profiling_dispatched` all reset the row and delete its partial per-test-case rows, so each first runs a status-filtered `UPDATE ... WHERE status IN (QUEUED, DISPATCHED, JUDGING/RUNNING)` and raises `JobNotDispatchable` (a `LookupError` subclass) when it matches nothing — before deleting anything. `dispatch_job` treats that as "this job cannot be processed", cleans up the job's Valkey state, and counts it as `lookup_error`.

This is a **fence, not a claim**: `DISPATCHED`/`JUDGING` are accepted so a legitimate retry after a reaper requeue still works, and two workers holding the same active job would both pass it. Mutual exclusion comes from the queue protocol and the Valkey lock. What the fence guarantees is narrower: a dequeue that races with the job's own completion can no longer reset the row or delete the results it already produced. Because the fence is a `LookupError` *subclass*, the legacy web→Arena fallback in `dispatch_job` can tell "this id is not a web submission" apart from "this web judgment is already terminal", and does not retry the latter in the Arena domain.
- The worker writes detailed judgment state transitions and audit entries back to PostgreSQL.
# Custom validator validation

See [../../docs/CUSTOM_VALIDATOR.md](../../docs/CUSTOM_VALIDATOR.md) for the author-facing
contract (exit codes, limits, how to write a validator). This section covers the worker side.

Custom validator candidates share the profiling-priority queue. The worker
loads the globally active language registry, compiles the candidate in a
disposable compile container, caps diagnostics, and promotes only the matching
candidate token. Unknown or inactive languages produce an `INVALID` candidate.

Interactive verdicts use the validator's clean exit code: `0` is `AC`, `1` is
`WA`, `2` is `TLE`, and `4` is `PE`. Any other clean exit is contestant `RE`.
Signals, startup or communication failures, and watchdog expiration are
internal failures and are eligible for one retry.

The validator is **parametrized by test-case input**, so the worker iterates the
problem's cases rather than running one conversation per submission. It loads the
case inputs (`NNN.in`; no `.out` is read or required), acquires **one** container
pair for the whole judgment, copies both compiled artifacts in once, and then per
case resets the run artifacts, re-inits isolate, starts a fresh process on each
side, and writes that case's input to the validator's stdin before relaying
anything. A case ending `AC` advances to the next; the first case that does not
stops the iteration and its verdict is the submission's. Judgment wall time and
memory are the worst readings across the cases that ran. An empty case list is an
internal `FAILED`, never a contestant verdict.

Container reuse across cases is safe because a case that ends `AC` ends cleanly —
the bridge only kills containers on OLE, watchdog, or a communication failure, and
all of those end the iteration anyway. A retryable failure therefore always
releases the pair and reacquires a fresh one. Resetting a run clears only stdout,
stderr and the isolate meta file, so the artifacts copied in at the start survive
every case.

For each submission, the active validator is compiled again; binaries are never
cached. Contestant and validator artifacts are ready before run containers are
acquired. Either container may consume a prewarmed one from `PoolManager`, and the
validator does not consume another worker slot. Both are destroyed afterward.

The contestant runs through isolate with the problem memory and PID limits but
without the problem CPU or wall-time limits. The trusted validator is supervised in
its separate network-disabled container without problem resource limits. Docker
SDK bidirectional exec sockets carry the full-duplex protocol, including
half-close/EOF propagation. Contestant stdout alone counts toward OLE — the
test-case input written to the validator does not — and the two retained stderr
streams are independently excerpt-capped. The watchdog and the output-limit counter
both apply per case.

Because every byte the two sides exchange is relayed through the bridge, both
stdout streams are recorded together as one ordered, line-split `transcript` (the
two former per-side stdout excerpts are gone). The test-case input fed to the
validator is deliberately not recorded: it is the problem's data, not part of the
conversation. Ordering is *as observed by the judge*: the two pumps are separate
tasks, so this is not a causal proof, but these protocols are strict
request/response — neither side can speak until the peer's line has been relayed
to it — so observed order is protocol order. Recording is capture-only and cannot
affect a verdict: past its 256 KiB cap the transcript is flagged `truncated` and
stops growing, while the pump keeps reading, keeps relaying, keeps counting
contestant output bytes, and still trips the real output limit.

Attempt rows are written per case and tagged with `test_case_ordinal`, but a
judgment retains only the **last executed case's** attempts: starting a case's
first attempt clears the judgment's earlier rows, so the surviving one or two rows
are the round that actually decided the submission.

Pending validation jobs are durable database state. Reconciliation recreates a
missing profiling-queue job, and the reaper requeues stale validation work up
to the ordinary retry ceiling. A validation that exhausts its retries is
flagged (`reaper_dropped`) rather than silently discarded: the reconciler then
marks the matching pending candidate `INVALID` with an explanatory compile log
so the uploader can stage it again. Token matching suppresses results from
removed or superseded candidates.

Arena crash containment applies only after two attempts at the same test case
without a clean validator exit. It marks the active revision `RUNTIME_FAILED`,
disables the problem, fails and dequeues other queued judgments, and emits one
owner notification. Clean undocumented exits, contestant failures/limits, and
pre-interaction compilation failures do not create strikes.
