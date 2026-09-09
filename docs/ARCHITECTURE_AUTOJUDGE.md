# NOCA Autojudge Module Architecture

This document describes the `autojudge/` module: the asynchronous worker that
consumes queued judgments, compiles and runs untrusted submissions inside
containers, and writes verdicts back. It is deliberately short, because the
judge's internals are documented where they are implemented; this page states
what the module owns and points to the documents that cover the rest. Read
[ARCHITECTURE.md](ARCHITECTURE.md) first for the boundary that keeps the judge
isolated from the HTTP processes.

Related references:
- [ARCHITECTURE.md](ARCHITECTURE.md) for the system overview and the infrastructure boundary
- [autojudge/docs/AUTOJUDGE_INFRA.md](../autojudge/docs/AUTOJUDGE_INFRA.md) for worker isolation, queue protocol, and container execution details, including how a per-run time limit becomes a test case's shared budget and the run-phase watchdog that outlives it
- [DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md](DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md) for the submission lifecycle
- [CONTAINER_STARTUP_OPTIONS.md](CONTAINER_STARTUP_OPTIONS.md) for the judge container Dockerfile and startup behavior
- [ARCHITECTURE_RUNTIME.md](ARCHITECTURE_RUNTIME.md) for the judging architecture and the communication model with `web`

## Responsibilities

The autojudge module is a separate async worker process. It owns queued submission
and profiling jobs, Docker container pool management, compilation, isolate-based
execution, result persistence, stale in-flight recovery, startup and periodic
reconciliation of non-terminal jobs missing from the queue (recovering jobs lost
between a producer's DB commit and its follow-up enqueue), zombie container
cleanup, and worker heartbeat health monitoring.

It also owns one piece of Arena progress state. Every Arena judgment that
finishes with a verdict or `FAILED` reconciles the
submitter's `arena_problem_solvers` row against the submissions that are still
Accepted (`autojudge/db/_arena_solver.py`), so a rejudge that withdraws an AC
stops the user counting as a solver and one that moves the first AC re-anchors
`solved_at`. The write happens in the judgment's own transaction under a
per-`(user, problem)` advisory lock. The derived counters in
`arena_problem_ratings` are not maintained here -- the rating worker rebuilds
them from these rows, and the judge cannot safely increment them because an
absent solver row does not distinguish a first solve from a withdrawn one. See
[DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md](DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md).

## What the judge shares with the other modules

The judge never imports `web` or `arena`. It reads and writes the shared schema
through its own focused data access layer (`autojudge/db/`), ignores the ORM
hooks the web module layers on top of the same tables, and reads test-case
files directly from the domain subdirectory of `NOCA_PROBLEM_TESTCASE_DIR`. Its
container is a pure schema consumer: the entrypoint waits for the schema to
reach the head revision it expects rather than running migrations itself, so a
mismatched worker image cannot drive the schema during a rolling deploy.

Several behaviors the judge implements are documented with the feature that
needs them rather than here:

- Custom interactive validators, the per-test-case replay, and the stall
  watchdog are in [ARCHITECTURE_SHARED.md](ARCHITECTURE_SHARED.md).
- The non-scoring solution-test jobs, which the worker processes without a
  Valkey handle so they can never publish a verdict or touch the scoreboard,
  are in [ARCHITECTURE_WEB.md](ARCHITECTURE_WEB.md).
- Pause and resume from the Arena dashboard, which the judge reconciles from
  PostgreSQL on every poll, is in [ARCHITECTURE.md](ARCHITECTURE.md) and
  [SHARED_SERVICES.md](SHARED_SERVICES.md).
