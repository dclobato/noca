# NOCA AI Assistant Module Architecture

This document describes the `aiassistant/` module: the worker that dequeues
Arena AI review jobs, calls the OpenAI Responses API for user-key reviews,
submits OpenAI Batch API jobs for platform-key reviews, and stores the feedback
in the database. It covers what the worker owns, the loops it runs in one
deployment unit, and the reconciler that is the only recovery path for a lost
queue job. Read [ARCHITECTURE.md](ARCHITECTURE.md) first for the module
boundary.

Related references:
- [ARCHITECTURE.md](ARCHITECTURE.md) for the system overview and the boundary between modules
- [AIASSISTANT.md](AIASSISTANT.md) for the worker's configuration and operation
- [AIREVIEW_FLOW.md](AIREVIEW_FLOW.md) for the end-to-end AI review flow
- [ARCHITECTURE_ARENA.md](ARCHITECTURE_ARENA.md) for the Arena request route that enqueues jobs

## Responsibilities

The aiassistant module is a standalone async worker. It owns dequeuing Arena AI
review jobs from the Valkey `ai:queue:pending` list, calling the OpenAI Responses
API when the submitting user has a personal `ai_api_key`, and submitting an
OpenAI Batch API job when the worker falls back to the platform key configured
via `NOCA_AI_OPENAI_API_KEY`. Online user-key jobs store the AI review immediately.
Platform-key jobs first insert a durable `arena_ai_batch_jobs` row with
`local_status='staged'`, without calling OpenAI. The batch flusher periodically
collects all staged rows into one multi-item OpenAI batch, and the batch poller
stores each result after OpenAI completes that batch.

## Loops

The worker runs the dequeue loop, stale-job reaper, batch flusher, batch poller,
reconciler, and worker-presence loop in one deployment unit. It also runs the
signed command loop when a worker command secret is configured. The reaper uses
`ai:queue:inflight:times` to recover queue jobs that were dispatched but not
cleaned up. The batch flusher wakes every five batch-poll intervals, or when
triggered. It submits all staged jobs as one OpenAI batch. The batch poller
reads non-terminal `arena_ai_batch_jobs` rows, retrieves OpenAI batch status,
stores completed review output in `arena_submission_ai_reviews`, creates Arena
notifications, clears failed retry flags, and deletes uploaded OpenAI files
after terminal states. At the top of each batch poll cycle, a stale-batch
detector locally expires batch jobs whose `submitted_at` is older than
`NOCA_AI_BATCH_STALE_HOURS`: in one transaction per submission it atomically
claims the row, refunds the consumed platform credit, clears `submit_to_ai`,
notifies the user, and finalizes the row as `expired`, then best-effort cancels
the OpenAI batch and deletes its files.

After any poll cycle that completes a batch, the worker derives turnaround
statistics from the 100 most recent successful platform-key reviews and stores
one persistent JSON value at `ai:batch:turnaround:stats`. The value is an
optional cache that Arena reads for the AI credits dashboard and platform-credit
review confirmation modal. Missing or invalid cache data produces an explicit
unavailable state; PostgreSQL remains authoritative, and a Valkey write failure
does not affect completed reviews.

## Reconciler

The reconciler is the database-driven safety net for the request route's dual
write: because
`arena_submissions.submit_to_ai=True` is committed to PostgreSQL before the job
is pushed to Valkey, a crash between the two leaves a flagged submission with no
queue job. The reconciler periodically finds such submissions (flagged, no
review row, no active batch job, older than a grace window) that have no live
pending/inflight queue presence and re-enqueues them. It is the *only*
recovery path: the Arena request route answers a flagged submission with the
pending state and never re-enqueues, because `ai:queue:pending` has no dedupe
and a route-side "self-heal" let an owner push unlimited duplicate jobs at no
cost (each one an OpenAI call when dequeued) while re-deriving
`use_platform_key` from the user's *current* key rather than the frozen one.
The route instead serializes overlapping first requests on the submission row
(`SELECT … FOR UPDATE`) and counts every request per user in a Valkey fixed
window (bucket `arena:ai-review`) before any lookup.
