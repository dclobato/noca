### (Auto)Judge Infrastructure

- The judge worker is a separate async process in `judge/` and does not import from `web/`.
- API and judge communicate only through PostgreSQL, Redis, and the shared test-case filesystem.
- Worker concurrency is fixed-width: one async consumer loop per slot, plus a reaper loop and a reconciler loop.
- Queue protocol:
  - priority queue: `judge:queue:priority`
  - normal queue: `judge:queue:pending`
  - inflight list: `judge:queue:inflight`
  - per-job hash: `judge:job:<judgment_id>`
    - expected fields for web jobs: `judgment_id`, `contest_id`, `is_rejudge`, `requeue_count`, `job_kind`
    - expected fields for Arena jobs: `judgment_id`, `submission_id`, `user_id`, `problem_id`, `language_id`, `requeue_count`, `job_kind`
    - optional traceability field: `submission_id`
    - rollout compatibility: older hashes may miss `contest_id`; tooling must bucket them as unknown
    - `job_kind` distinguishes normal `submission`, `arena_submission`, and `profiling` jobs used by Auto-Limit profiling
  - result channel: `judge:results`
- Jobs are intentionally lightweight in Redis. The worker dequeues a `judgment_id` and loads the full submission payload from PostgreSQL.
- Arena jobs share the normal pending queue and the same compile/run containers as web jobs. Their adapter reads test-case ordinals from Arena database rows and loads the content from the shared filesystem at `<NOCA_PROBLEM_TESTCASE_DIR>/arena/<problem_id>/NNN.in|out` (web jobs read `<root>/contest/<problem_id>`), stores only the first non-AC test result, and does not publish `judge:results` events.
- The worker uses a Redis `SET NX` lock per judgment to avoid duplicate processing after requeue races.
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
- The worker writes detailed judgment state transitions and audit entries back to PostgreSQL.
