# Arena AI Review Flow

This document describes the end-to-end life cycle of an Arena AI code review
request, from the user clicking "Request AI Review" through to the review being
displayed on the submission detail page.

For the worker module boundary, runtime loops, and security guardrails, see
[AI assistant module](AIASSISTANT.md).

There are two distinct execution paths:

- **Online path (fast-track)** — used when the user has configured a personal
  OpenAI API key. The review is produced synchronously and stored immediately,
  typically within a few seconds.
- **Batch path** — used when no user key exists and the platform key
  (`NOCA_AI_OPENAI_API_KEY`) is configured. The review is submitted to the OpenAI
  Batch API and polled for completion. Results typically arrive within a few
  hours, up to 24 hours.

---

## Table of Contents

- [1. Overview](#1-overview)
- [2. Key components](#2-key-components)
- [3. Valkey data structures](#3-valkey-data-structures)
- [4. Database tables](#4-database-tables)
- [5. Online path (fast-track)](#5-online-path-fast-track)
- [6. Batch path](#6-batch-path)
- [7. Stale-job reaper](#7-stale-job-reaper)
- [8. Idempotency and crash safety](#8-idempotency-and-crash-safety)
- [9. User notifications](#9-user-notifications)
- [10. UI states](#10-ui-states)
- [11. Configuration reference](#11-configuration-reference)

---

## 1. Overview

```
Arena user
    │
    │  POST /submissions/{id}/request-ai-review
    ▼
arena (FastAPI)
    │
    ├── credit gate:
    │     user.ai_api_key set?  → use_platform_key=False  (no credit consumed)
    │     ai_backend_credits≥1? → use_platform_key=True   (consume one credit atomically)
    │     else                  → 303 redirect with flash error (no enqueue)
    │
    ├── sets submit_to_ai=True in arena_submissions
    ├── writes job hash (including use_platform_key) to ai:job:{submission_id}
    └── LPUSH submission_id → ai:queue:pending
                │
                ▼
           Valkey: ai:queue:pending (LIST)
                │
    ┌───────────┘ BLMOVE (atomic)
    │
    ▼
aiassistant worker (dequeue loop)
    │
    ├── reads use_platform_key from ai:job:{submission_id} hash (decision frozen at request time)
    │       use_platform_key=False → Online path (Responses API, sync) ──► result stored immediately
    │       use_platform_key=True  → Batch path (Batch API, async)     ──► poller stores result later
    │
    ▼
arena_submission_ai_reviews (PostgreSQL)
arena_notifications (PostgreSQL)
    │
    ▼
arena (FastAPI) displays review on submission detail page
```

---

## 2. Key components

| Component | Location | Responsibility |
|-----------|----------|---------------|
| Arena HTTP layer | `arena/routes/submissions.py` | Accepts the review request, enforces credit gate, consumes one `ai_backend_credits` when using platform key, sets `submit_to_ai`, enqueues the job with frozen `use_platform_key` |
| Valkey queue | `ai:queue:pending` LIST | Holds pending `submission_id` strings |
| Valkey inflight | `ai:queue:inflight` LIST + `ai:queue:inflight:times` ZSET | Tracks jobs being processed; used by the reaper |
| Valkey job hash | `ai:job:{submission_id}` HASH | Stores recovery metadata while the Valkey job is active; terminal cleanup deletes it |
| `aiassistant` worker — dequeue loop | `aiassistant/worker.py` | Dequeues jobs, dispatches to online or batch path |
| `aiassistant` worker — reaper loop | `aiassistant/reaper.py` | Recovers stale inflight jobs |
| `aiassistant` worker — reconciler loop | `aiassistant/reconciler.py` | Re-enqueues jobs lost between the request route's DB commit and Valkey enqueue |
| `aiassistant` worker — batch poller | `aiassistant/batch_poller.py` | Polls OpenAI for completed batches, stores results |
| Online reviewer | `aiassistant/reviewer.py` | Synchronous OpenAI Responses API call |
| Batch reviewer | `aiassistant/batch_reviewer.py` | Builds JSONL, uploads files, calls `batches.create` |
| Batch job table | `arena_ai_batch_jobs` | Durable state machine for each submitted batch |
| Review result table | `arena_submission_ai_reviews` | Stores the final AI response text and cost |
| Notifications table | `arena_notifications` | `AI_REVIEW_COMPLETED` or `AI_REVIEW_FAILED` events |

---

## 3. Valkey data structures

| Key | Type | Purpose |
|-----|------|---------|
| `ai:queue:pending` | LIST (LPUSH/BRPOPLPUSH) | FIFO queue of `submission_id` strings awaiting processing |
| `ai:queue:inflight` | LIST | `submission_id` strings currently being processed by the worker |
| `ai:queue:inflight:times` | ZSET (score = epoch seconds) | Dispatch timestamps; used by the reaper to detect stale jobs |
| `ai:job:{submission_id}` | HASH | `user_id`, `problem_id`, `language_id`, `use_platform_key`, `requeue_count` |
| `ai:batch:turnaround:stats` | STRING (JSON) | Statistics for the 100 most recent successful platform-key reviews |

The atomic dequeue uses a Lua script that moves the item from `ai:queue:pending`
to `ai:queue:inflight` in a single operation, preventing double-processing even
under concurrent workers.

---

## 4. Database tables

| Table | Relevant columns | Purpose |
|-------|-----------------|---------|
| `arena_submissions` | `submit_to_ai` (bool) | Set to `True` when a review is requested; cleared on failure so the user can retry |
| `arena_submission_ai_reviews` | `submission_id`, `ai_response`, `ai_response_at`, `_ai_review_cost`, `used_platform_key` | Stores the completed review result |
| `arena_ai_batch_jobs` | `submission_id`, `openai_batch_id`, `local_status`, `openai_status`, `input_file_id`, `code_file_id`, `statement_file_id`, `error_file_id`, `last_error`, `request_counts_*`, `last_polled_at`, `submitted_at`, `completed_at` | Durable state machine for batch-path jobs (`submitted_at` drives stale detection) |
| `arena_notifications` | `notification_kind`, `source_ref`, `message` | `AI_REVIEW_COMPLETED` or `AI_REVIEW_FAILED` notification row |
| `security_events` | `event_type`, `actor_user_id`, `metadata` | Stores `ai_response_redacted` audit rows when output guardrails suppress code-like content |

---

Both paths apply the security guardrails documented in
[AI assistant module](AIASSISTANT.md#security-guardrails). The worker wraps
uploaded source and statement files in untrusted-data boundaries, tells the
model not to follow instructions inside those files, redacts fenced code blocks
and overlong code-like output before storage, and records an
`ai_response_redacted` security event when redaction occurs.

## 5. Online path (fast-track)

Used when `user.ai_api_key` was set at request time (`use_platform_key=False` in the job hash).

```
arena HTTP ──► credit gate passed (user has own API key)
           ──► use_platform_key=False frozen in job hash
           ──► submit_to_ai=True, LPUSH ai:queue:pending
                            │
                    dequeue loop (BLMOVE)
                            │
                    reads use_platform_key=False from hash
                            │
                    _process_job(use_platform_key=False)
                            │
                      idempotency checks
                      (already reviewed? active batch? → skip)
                            │
                    api_key = user_api_key          ← user's own key
                    is_platform_key = False
                            │
                    _process_job_online()
                            │
                    ┌───────────────────────────────────────┐
                    │  reviewer.call_ai_review()            │
                    │                                       │
                    │  1. write source code to temp file    │
                    │  2. write problem statement to temp   │
                    │  3. files.create(code, user_data)     │
                    │  4. files.create(stmt, user_data)     │
                    │  5. responses.create(                 │
                    │       model, instructions,            │
                    │       input=[input_file, input_file,  │
                    │              input_image?, input_text]│
                    │     )                                 │
                    │  6. delete both OpenAI files          │
                    │  7. delete both temp files            │
                    └───────────────────────────────────────┘
                            │
                    ┌── AuthenticationError / PermissionDeniedError?
                    │     clear submit_to_ai flag
                    │     store AI_REVIEW_FAILED notification
                    │     complete_arena_ai_review_job()
                    │     (no retry — bad user key)
                    │
                    └── success:
                          store_ai_review_result()
                            → INSERT arena_submission_ai_reviews
                          store_ai_review_completed_notification()
                            → INSERT arena_notifications (AI_REVIEW_COMPLETED)
                          complete_arena_ai_review_job()
```

**Request body structure (OpenAI Responses API):**

```json
{
  "model": "gpt-5.4-mini",
  "instructions": "<SYSTEM_PROMPT>",
  "max_output_tokens": 500,
  "input": [
    {
      "role": "user",
      "content": [
        { "type": "input_file",  "file_id": "<code_file_id>" },
        { "type": "input_file",  "file_id": "<stmt_file_id>" },
        { "type": "input_image", "image_url": "data:<mime>;base64,..." },  ← only when problem has an image
        { "type": "input_text",  "text": "Analyze the submitted program…<lang_context>" }
      ]
    }
  ]
}
```

**Cost recording:**  
`cost = (input_tokens / 1_000_000) × OPENAI_INPUT_TOKEN_PRICE + (output_tokens / 1_000_000) × OPENAI_OUTPUT_TOKEN_PRICE`  
Stored as integer micros (`round(cost × 1_000_000)`) in `_ai_review_cost`.
`used_platform_key = False`.

---

## 6. Batch path

Used when the user had no personal API key at request time (`use_platform_key=True` in the job hash). One `ai_backend_credits` credit was consumed atomically by the Arena HTTP layer before enqueuing. Requires `NOCA_AI_OPENAI_API_KEY` to be set on the worker side.

### 6.1 Submission phase (dequeue loop)

```
dequeue loop → reads use_platform_key=True from hash
             → _process_job(use_platform_key=True)
                    │
              api_key = platform key     ← NOCA_AI_OPENAI_API_KEY
              is_platform_key = True
                    │
              _process_job_batch()
                    │
              ┌──────────────────────────────────────────────────────┐
              │  batch_reviewer.submit_ai_batch_review()             │
              │                                                      │
              │  1. write source code to temp file                   │
              │  2. write problem statement to temp file             │
              │  3. files.create(code, purpose="user_data")          │
              │     → code_file_id                                   │
              │  4. files.create(stmt, purpose="user_data")          │
              │     → statement_file_id                              │
              │  5. build JSONL line:                                │
              │     { "custom_id": submission_id,                    │
              │       "method": "POST",                              │
              │       "url": "/v1/responses",                        │
              │       "body": { model, instructions,                 │
              │                 max_output_tokens,                   │
              │                 input: [input_file, input_file,      │
              │                         input_image?, input_text] }} │
              │  6. files.create(jsonl, purpose="batch")             │
              │     → input_file_id                                  │
              │  7. batches.create(                                   │
              │       input_file_id=input_file_id,                   │
              │       endpoint="/v1/responses",                      │
              │       completion_window="24h"                        │
              │     ) → openai_batch_id                              │
              │  8. delete three temp files from disk                │
              │  *** OpenAI files are NOT deleted here ***           │
              └──────────────────────────────────────────────────────┘
                    │
              ┌── AuthenticationError / PermissionDeniedError?
              │     clear submit_to_ai flag
              │     store AI_REVIEW_FAILED notification
              │     complete_arena_ai_review_job()
              │     (no retry — bad platform key)
              │
              └── success:
                    (a) insert_batch_job()
                          → INSERT arena_ai_batch_jobs
                            (local_status = 'submitted')
                    (b) complete_arena_ai_review_job()
```

> **Crash safety:** `(a)` writes the DB row before `(b)` removes the pending and
> inflight entries, dispatch timestamp, and job hash from Valkey. If the
> process crashes between the two, the reaper re-enqueues the job. On
> re-processing, the idempotency guard detects the existing
> `arena_ai_batch_jobs` row and skips without creating a duplicate batch.

### 6.2 `arena_ai_batch_jobs` local status state machine

```
                  [submitted]
                      │
          ┌───────────┘  (first poller cycle)
          │
          ▼
       [polling]  ◄─────────────────── (every poll cycle, non-terminal)
          │
          ├── openai_status = "completed"  ──► [completed]
          ├── openai_status = "failed"     ──► [failed]
          ├── openai_status = "expired"    ──► [expired]
          ├── openai_status = "cancelling"
          │   openai_status = "cancelled"  ──► [cancelled]
          │
          └── submitted_at older than AI_BATCH_STALE_HOURS (stale detector):
                  claim ──► [expiring] ──(commit)──► [expired]
```

`expiring` is a transient in-transaction sentinel used only by the stale-batch
detector to atomically claim a row before refunding credit and notifying the
user. At READ COMMITTED isolation other connections never observe it: the row is
invisible until the transaction commits, at which point it is already `expired`.
It is **not** a terminal status (so a crashed mid-expiry row is re-detected and
retried), but it is excluded from the pollable set and from `update_batch_job_poll`.

OpenAI statuses that are tracked verbatim in `openai_status`:
`validating`, `in_progress`, `finalizing`, `completed`,
`failed`, `expired`, `cancelling`, `cancelled`.

### 6.3 Polling phase (batch poller loop)

The batch poller loop wakes every `AI_BATCH_POLL_INTERVAL_SECONDS` (default 300 s)
and processes every non-terminal `arena_ai_batch_jobs` row.

```
batch_poller.run_batch_poller_loop()
    │
    └── every AI_BATCH_POLL_INTERVAL_SECONDS:
            _expire_stale_batches()  ← runs first; see §6.4
            get_pending_batch_jobs()  ← rows where local_status NOT IN terminal set
                │
                └── for each job:
                        client.batches.retrieve(openai_batch_id)
                            │
                        update_batch_job_poll()   ← openai_status, last_polled_at,
                                                     request_counts_*, local_status
                            │
                        ┌── non-terminal (in_progress, validating, finalizing)?
                        │     → log and continue to next job
                        │
                        └── terminal status reached:
                                │
                        ┌───── openai_status = "completed"?
                        │           │
                        │   download output_file_id (JSONL)
                        │   for each line (custom_id = submission_id):
                        │       status_code == 200?
                        │           ├── YES: extract output_text from body.output[].content[]
                        │           │         compute cost from body.usage (input+output tokens)
                        │           │         store_ai_review_result()
                        │           │         store_ai_review_completed_notification()
                        │           └── NO:  clear submit_to_ai flag
                        │                     store_ai_review_failed_notification()
                        │
                        │   error_file_id present?
                        │       download error file (JSONL)
                        │       for each line:
                        │           clear submit_to_ai flag
                        │           store_ai_review_failed_notification()
                        │           record error_file_id on batch row
                        │
                        └───── failed / expired / cancelling / cancelled?
                                    clear submit_to_ai flag
                                    store_ai_review_failed_notification()
                                    record last_error on batch row
                                │
                        finalize_batch_job()  ← local_status, completed_at
                        _delete_openai_files()
                            delete: input_file_id, code_file_id,
                                    statement_file_id, error_file_id
                            (each deletion wrapped in contextlib.suppress —
                             a failure on one does not abort the others)
                │
                └── if at least one batch completed:
                        recompute turnaround statistics from PostgreSQL
                        SET ai:batch:turnaround:stats (one atomic JSON value)
```

The turnaround window contains the 100 most recently stored successful
platform-key reviews. Each duration is the non-negative whole number of seconds
from `arena_ai_batch_jobs.created_at` to
`arena_submission_ai_reviews.ai_response_at`. The persistent JSON payload
contains `version`, `average_seconds`, `median_seconds`, `stddev_seconds`,
`sample_count`, and `updated_at`. If no qualifying reviews exist, the poller
deletes the key. Publication is best-effort and cannot roll back completed
reviews. Arena validates this payload before showing its average, median,
standard deviation, and sample count on the AI credits dashboard. The
platform-credit confirmation modal shows the average and median; both views
render an unavailable state when the key is missing or invalid.

**Cost recording (batch path):**  
`cost = (input_tokens / 1_000_000) × effective_batch_input_price + (output_tokens / 1_000_000) × effective_batch_output_price`  
`effective_batch_*_price` defaults to `OPENAI_*_TOKEN_PRICE / 2` (the ~50 % batch discount).
Overrideable via `NOCA_AI_OPENAI_BATCH_INPUT_TOKEN_PRICE` / `NOCA_AI_OPENAI_BATCH_OUTPUT_TOKEN_PRICE`.  
`used_platform_key = True`.

### 6.4 Stale-batch detector (`_expire_stale_batches`)

OpenAI batch jobs can hang without reaching a terminal status — either because of
a pipeline issue on our side or at OpenAI. When that happens the user's platform
credit is already consumed, `submit_to_ai=True` blocks a re-request, and the
review never arrives. The stale-batch detector runs at the **top of every batch
poll cycle**, before `get_pending_batch_jobs`.

```
_expire_stale_batches()
    │
    threshold = now - AI_BATCH_STALE_HOURS
    get_stale_batch_jobs(threshold)  ← staleness decided PER openai_batch_id:
                                        MAX(submitted_at of non-terminal rows) <= threshold
                                        AND the batch has NO 'completed' row
                                        (expiring rows included → crash recovery)
        │
        └── group by openai_batch_id; for each group:
                claimed = handle_stale_batch()  ← one engine.begin() per submission:
                    claim_batch_job_for_expiry()  → [expiring]  (skip if already claimed/terminal)
                    get_submission_for_review()
                    clear_submit_to_ai_flag()
                    refund_ai_credit_for_submission()   (+1 credit, 'refund' txn row)
                    store_ai_review_stale_notification()
                    finalize_expired_batch_job()  → [expired], completed_at
                │
                if len(claimed) == len(jobs) and platform key:   ← whole group expired only
                    _cancel_and_cleanup()
                        client.batches.cancel(openai_batch_id)   (best-effort)
                        delete_openai_files() per job
                        record_batch_cleanup_error()  ← durable last_error on any failure
```

**Group-level staleness (no partial expiry).** Staleness is decided per
`openai_batch_id`, not per row. A batch is expired only when the *newest* of its
non-terminal rows crossed the threshold (`MAX(submitted_at) <= threshold`), so
legacy rows whose `submitted_at` was backfilled per-row from `created_at` can
never cross independently of their siblings. A batch with any `completed` row is
excluded entirely — its results are arriving and must not be expired or have its
OpenAI files deleted.

**Atomic per-submission expiry.** The claim → refund → notify → finalize sequence
runs in **one transaction per submission**, so a crash mid-step rolls the row back
to its pre-claim status and the next cycle retries. Because the row is only
`expired` after a successful commit, a credit can never be refunded without the row
being finalized, and the atomic claim (`UPDATE … RETURNING` under a row lock)
guarantees no double-refund even with two workers racing. Every
`arena_ai_batch_jobs` row is a platform-key submission, so each stale row always
warrants a refund. When the submission was deleted, the row is still finalized as
`expired` (no credit, no notification).

**Cancellation only on full-group expiry.** `handle_stale_batch` returns the rows
it actually claimed; the OpenAI batch is cancelled and its files deleted only when
this worker expired the *entire* group, so a batch another worker is completing is
never cancelled. Cancellation and file-deletion failures are non-fatal but recorded
durably in `last_error`. The normal poller is protected symmetrically:
`update_batch_job_poll` skips `expiring`/terminal rows and returns the affected-row
count, and `_process_batch` bails when that count is `0` (the batch was already
expired), so a race can never produce both a refund and a stored review.

Configurable via `NOCA_AI_BATCH_STALE_HOURS` (default 24 h).

---

## 7. Stale-job reaper

The reaper loop wakes every `AI_REAPER_INTERVAL_SECONDS` (default 60 s) and
scans `ai:queue:inflight:times` for jobs dispatched more than
`AI_STALE_THRESHOLD_SECONDS` (default 300 s) ago.

```
reaper loop wakes
    │
    get_stale_ai_review_job_ids()
      ← ZRANGEBYSCORE ai:queue:inflight:times 0 (now - stale_threshold)
        │
        └── for each stale submission_id:
                get_ai_review_job_hash()
                    │
                ┌── hash missing?  (worker already finished, ZSET not yet cleaned)
                │     remove_from_ai_review_inflight()
                │     done
                │
                └── hash present:
                        requeue_count = hash.requeue_count + 1
                        use_platform_key = hash.use_platform_key  ← preserved from original job
                    ┌── requeue_count > AI_MAX_REQUEUE_COUNT?
                    │     complete_arena_ai_review_job()
                    │       → delete queue entries, timestamp, and hash
                    │     log warning, discard job
                    │
                    └── remove_from_ai_review_inflight()
                        enqueue_arena_ai_review_job()
                          LPUSH ai:queue:pending  (incremented requeue_count, same use_platform_key)
```

On re-enqueue, `_process_job` runs the idempotency checks again:
- If an `arena_submission_ai_reviews` row now exists → skip (online path completed).
- If a non-terminal `arena_ai_batch_jobs` row exists → skip (batch already submitted).

---

## 8. Idempotency and crash safety

| Scenario | Protection |
|----------|-----------|
| Review requested twice (double-click) | Arena HTTP layer returns early when an `arena_submission_ai_reviews` row exists; when only `submit_to_ai=True` it re-enqueues idempotently to self-heal a lost job — no credit charged on the duplicate |
| Request route crashes (or Valkey enqueue fails) after the `submit_to_ai=True` commit but before the enqueue | The reconciler loop finds the flagged submission with no pending/inflight queue presence and re-enqueues it; a user re-request also self-heals it |
| Re-request after a previous attempt failed (terminal `arena_ai_batch_jobs` row, no review) | Dequeue loop's guard deletes the spent terminal batch row and submits a fresh batch instead of skipping |
| Worker crashes after API call but before `complete_arena_ai_review_job` | Reaper re-enqueues; dequeue loop finds existing `arena_submission_ai_reviews` row and performs terminal cleanup |
| Worker crashes after `batches.create` but before `insert_batch_job` | Orphaned OpenAI batch and files; reaper re-enqueues; no DB row found → new batch submitted (old files must be cleaned manually) |
| Worker crashes after `insert_batch_job` but before `complete_arena_ai_review_job` | Reaper re-enqueues; dequeue loop finds the non-terminal `arena_ai_batch_jobs` row and performs terminal cleanup |
| Batch poller crashes mid-output-processing | `finalize_batch_job` not called; job remains non-terminal; next poller cycle retries the whole output parse (result storage uses `ON CONFLICT DO NOTHING`) |

---

## 9. User notifications

Both paths send an `arena_notifications` row when complete.

| Event | `notification_kind` | When sent |
|-------|-------------------|-----------|
| Review finished successfully | `AI_REVIEW_COMPLETED` | After `store_ai_review_result` |
| Review failed (bad key, provider error, expired batch, per-request failure) | `AI_REVIEW_FAILED` | After `clear_submit_to_ai_flag` |
| Batch went stale (locally expired by the stale detector after `AI_BATCH_STALE_HOURS`) | `AI_REVIEW_FAILED` ("AI review timed out") | After `refund_ai_credit_for_submission`, inside the expiry transaction |

The Arena HTTP layer reads these rows and displays them in the notification bell.
`source_ref` contains the `submission_id` so the notification links directly to
the submission detail page.

---

## 10. UI states

The submission detail page (`arena/template/submissions/submission_detail.html`)
shows one of four states for the AI review section, evaluated in this order:

| Priority | Condition | UI displayed |
|----------|-----------|-------------|
| 1 | `ai_review` row exists | Review text, timestamp, and cost (if recorded) |
| 2 | `submission_submit_to_ai = True` (online path pending) | "AI review in progress…" spinner |
| 3 | `batch_local_status` is non-terminal (batch path pending) | "Review queued for batch processing — results usually ready within a few hours (up to 24 h)" |
| 4 | None of the above | "Request AI Review" button |

The `batch_local_status` is surfaced by LEFT JOINing `arena_ai_batch_jobs` in
the submission detail query (`arena/routes/submissions.py`).

---

## 11. Configuration reference

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_AI_OPENAI_API_KEY` | *(unset)* | Platform-level API key. Unset = batch path inactive; user keys only |
| `NOCA_AI_OPENAI_MODEL` | `gpt-5.4-mini` | OpenAI model for all reviews |
| `NOCA_AI_OPENAI_MAX_OUTPUT_TOKENS` | `500` | Maximum output tokens per review |
| `NOCA_AI_OPENAI_INPUT_TOKEN_PRICE` | `0.75` | USD per 1M input tokens (online path cost recording) |
| `NOCA_AI_OPENAI_OUTPUT_TOKEN_PRICE` | `4.50` | USD per 1M output tokens (online path cost recording) |
| `NOCA_AI_OPENAI_BATCH_INPUT_TOKEN_PRICE` | *(unset — half of online)* | USD per 1M input tokens (batch path). Defaults to `OPENAI_INPUT_TOKEN_PRICE / 2` |
| `NOCA_AI_OPENAI_BATCH_OUTPUT_TOKEN_PRICE` | *(unset — half of online)* | USD per 1M output tokens (batch path). Defaults to `OPENAI_OUTPUT_TOKEN_PRICE / 2` |
| `NOCA_AI_POLL_INTERVAL_SECONDS` | `5.0` | Seconds between queue polls when `ai:queue:pending` is empty |
| `NOCA_AI_STALE_THRESHOLD_SECONDS` | `300.0` | Age in seconds after which an inflight job is treated as stale |
| `NOCA_AI_REAPER_INTERVAL_SECONDS` | `60.0` | Seconds between reaper scans |
| `NOCA_AI_MAX_REQUEUE_COUNT` | `3` | Maximum re-enqueue attempts before discarding a stale job |
| `NOCA_AI_BATCH_POLL_INTERVAL_SECONDS` | `300.0` | Seconds between batch poller cycles (60–3600) |
| `NOCA_AI_BATCH_STALE_HOURS` | `24` | Hours after `submitted_at` before a non-terminal batch job is locally expired (refund + notify + cancel), 1–168 |
| `NOCA_AI_RECONCILER_INTERVAL_SECONDS` | `120.0` | Seconds between reconciler sweeps for jobs lost after commit (min 10) |
| `NOCA_AI_RECONCILER_GRACE_SECONDS` | `120.0` | Minimum age before a flagged submission is reconciled, to avoid racing a fresh enqueue (min 10) |
| `NOCA_AI_RECONCILER_BATCH_SIZE` | `100` | Maximum lost jobs re-enqueued per reconciler sweep (1–1000) |
| `NOCA_CRYPTO_ENV_FILE` | `.env.crypto` | Path to the crypto env file required for `EncryptedString` decryption of user API keys |
