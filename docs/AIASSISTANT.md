# AI assistant module

The `aiassistant` module is the standalone Arena AI review worker. It keeps
external AI provider calls outside the Arena HTTP process, owns the online and
batch AI review execution paths, and stores completed reviews back into shared
Arena tables.

For the end-to-end user flow, see [Arena AI review flow](AIREVIEW_FLOW.md). This
page documents the module boundary, runtime loops, persistence points, and
security guardrails that module maintainers must preserve.

## Runtime role

The worker starts through the `noca-aiassistant` console script, backed by
`aiassistant/worker.py`. It reads common PostgreSQL, Valkey, logging, and crypto
settings, then runs the AI review loops in one async process.

The module owns these responsibilities:

- dequeue Arena AI review jobs from Valkey
- call the OpenAI Responses API for user-key reviews
- submit and poll OpenAI Batch API jobs for platform-key reviews
- recover stale or lost jobs through reaper and reconciler loops
- persist review output, review cost, batch job state, and notifications
- publish worker presence, last-job metadata, and batch turnaround statistics

The Arena HTTP app owns user-facing authorization, credit gating, and enqueueing.
The AI assistant trusts the queued job decision for `use_platform_key`, but it
still verifies database state before it stores output.

## Review execution paths

The worker has two review paths. Both paths upload the source code and problem
statement as OpenAI `user_data` files, send a structured review task, and store
the final text in `arena_submission_ai_reviews`.

### Online path

The online path runs when the submitting Arena user has a personal encrypted
OpenAI API key. `aiassistant/worker.py` loads the key, calls
`aiassistant/reviewer.py`, stores the result immediately, creates an
`AI_REVIEW_COMPLETED` notification, and completes the Valkey queue job.

Online reviews set `used_platform_key=False`. The cost is computed from returned
token usage when OpenAI provides usage data.

### Batch path

The batch path runs when the queued job is marked `use_platform_key=True` and
`NOCA_AI_OPENAI_API_KEY` is configured. `aiassistant/batch_reviewer.py` uploads
the files, creates a JSONL request, submits the OpenAI Batch API job, and writes
an `arena_ai_batch_jobs` row.

`aiassistant/batch_poller.py` later polls non-terminal batch rows. When OpenAI
returns a completed output file, `aiassistant/batch_results.py` extracts the
review text, stores the review, creates the user notification, updates local
batch state, and deletes uploaded OpenAI files on terminal states.

Batch reviews set `used_platform_key=True`. Platform credits are consumed by the
Arena request route before enqueueing. Stale batch expiry refunds that credit
when the batch never reaches a usable terminal result within
`NOCA_AI_BATCH_STALE_HOURS`.

## Runtime loops

`aiassistant/worker.py` runs several async loops together. Each loop has a
separate failure boundary so one loop can log an error without silently stopping
the whole worker.

- **Dequeue loop**: claims pending jobs from `ai:queue:pending` and dispatches
  them to the online or batch path.
- **Reaper loop**: scans `ai:queue:inflight:times` for stale jobs and requeues
  them up to `NOCA_AI_MAX_REQUEUE_COUNT`.
- **Reconciler loop**: finds submissions that are flagged for AI review in
  PostgreSQL but no longer have Valkey queue state, then re-enqueues them after
  `NOCA_AI_RECONCILER_GRACE_SECONDS`.
- **Batch poller loop**: polls active OpenAI batch jobs, stores completed
  output, expires stale local batches, and refreshes turnaround statistics.
- **Presence and command loops**: publish worker status and apply signed
  pause/resume, flush-now, and poll-now commands.

## Data stores

The module uses PostgreSQL as durable state and Valkey as queue and coordination
state. Valkey state is recoverable; PostgreSQL state is authoritative for
completed review output and batch lifecycle.

Important PostgreSQL tables include:

- `arena_submissions`
- `arena_submission_ai_reviews`
- `arena_ai_batch_jobs`
- `arena_ai_credit_transactions`
- `arena_notifications`
- `security_events`

Important Valkey keys include:

- `ai:queue:pending`
- `ai:queue:inflight`
- `ai:queue:inflight:times`
- `ai:job:{submission_id}`
- `ai:batch:turnaround:stats`

## Security guardrails

AI review input contains untrusted source code, problem statements, optional
extra instructions, and optional image captions. The worker uses explicit input
boundaries and output post-processing to reduce the chance that malicious review
context changes the intended behavior.

### Input boundaries

`aiassistant/guardrails.py` owns the shared guardrail helpers. The worker uses
`build_review_user_text()` to create the review task with explicit instructions
that uploaded code and statements are untrusted context, not commands.

The same module provides `wrap_untrusted_review_artifact()`. Online and batch
reviewers use it before writing uploaded source and statement temp files, so the
OpenAI file content itself includes untrusted-data markers.

These boundaries are used in:

- `aiassistant/reviewer.py` for online Responses API reviews
- `aiassistant/batch_reviewer.py` for single-item and windowed Batch API reviews

Do not replace these boundaries with prompt-injection stripping. Stripping is
brittle and can remove legitimate source code or statements. Keep the controls
as explicit boundaries plus output validation.

### Output redaction

The worker sanitizes model output before it stores or displays an AI review.
`sanitize_ai_review_response()` redacts fenced code blocks and overlong
code-like lines that look like solution output.

Sanitization happens in both result paths:

- `aiassistant/reviewer.py` sanitizes online Responses API output before it
  returns `ReviewResult`.
- `aiassistant/batch_results.py` sanitizes Batch API output before it calls
  `store_ai_review_result()`.

This protects the "hints only" product rule even when the model returns code.
It is intentionally conservative: it does not try to prove whether code is a
correct solution before redacting.

### Security events

When output is redacted, the worker records an `ai_response_redacted` event in
the shared `security_events` table. The event includes the submission id, path
(`online` or `batch`), redaction reason, and actor user id when available.

Admins can inspect these rows on the Arena admin security-events page. Use these
events to tune prompts, investigate repeated abuse, or decide whether stricter
review controls are needed.

## File lifecycle

OpenAI upload cleanup differs by path.

Online reviews delete uploaded files in the `finally` block inside
`aiassistant/reviewer.py`. The implementation tracks code and statement file ids
independently, so a partial upload failure still deletes any file that was
already uploaded.

Batch reviews keep uploaded files alive after `batches.create()` because OpenAI
needs those file ids while the batch is active. The batch poller deletes input,
output, and error files after terminal local handling. Stale local expiry also
best-effort cancels the batch and deletes files.

## Configuration

AI assistant settings use the `NOCA_AI_` prefix and are documented in
[Configuration](CONFIG.md#ai-assistant-worker). The key operational settings
are:

- `NOCA_AI_OPENAI_API_KEY`
- `NOCA_AI_OPENAI_MODEL`
- `NOCA_AI_OPENAI_MAX_OUTPUT_TOKENS`
- `NOCA_AI_BATCH_POLL_INTERVAL_SECONDS`
- `NOCA_AI_BATCH_STALE_HOURS`
- `NOCA_AI_STALE_THRESHOLD_SECONDS`
- `NOCA_AI_MAX_REQUEUE_COUNT`
- `NOCA_AI_RECONCILER_INTERVAL_SECONDS`

The module also reads shared database, Valkey, logging, worker-command, and
crypto settings.

## Testing

Keep tests focused on the boundary being changed. Useful test groups include:

- `tests/aiassistant/test_guardrails.py` for prompt boundaries and redaction
- `tests/aiassistant/test_reviewer.py` for online Responses API request shape
  and upload cleanup
- `tests/aiassistant/test_worker.py` for online worker persistence and queue
  completion
- `tests/aiassistant/test_reaper.py` for stale Valkey job recovery
- `tests/aiassistant/test_stale_batch_expiry.py` for local batch expiry and
  refund behavior

When you change shared queue helpers or live Valkey behavior, run the focused
Valkey-backed tests in an environment where Valkey DB 15 is available. In this
workspace, use `UV_CACHE_DIR=/tmp/uv-cache` with `uv run` commands.
