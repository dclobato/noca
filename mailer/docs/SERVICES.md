# Mailer worker — modules and contracts

The `mailer` module is the one NOCA process that talks to the email provider. The
Web and Arena HTTP processes render every outbound email and push it, fully
formed, onto the Valkey mail queue (see
[SHARED_SERVICES.md](../../docs/SHARED_SERVICES.md) — `email_service.py`,
`email_budget.py`, and the `valkey_service/` mail queue helpers); this worker
drains that queue at the deployment's own pace. It has no HTTP surface and no
`ROUTES.md`.

## `worker.py`

Entry point `main` (console script `noca-mailer`), with the watchfiles
hot-reload supervisor in development exactly as the other workers.

| Function | Description |
|---|---|
| `build_provider() -> EmailProvider` | `EmailConfig.from_settings(settings).create_worker_provider()` — the **only** place in NOCA that builds an `SMTPProvider`; the SMTP settings are validated here and nowhere else. `NOCA_SEND_EMAIL=false` or `NOCA_EMAIL_PROVIDER=mock` yields the mock provider, which drains the queue into the log. |
| `async process_job(job_id, valkey_runtime, provider, *, job_ttl_seconds, now=None) -> bool` | Deliver one dequeued job and settle its queue state. A missing or unreadable hash is discarded (the TTL expired it, or it was handled elsewhere) — unless the runtime reports Valkey unavailable, in which case the job is left inflight for the reaper rather than deleted on reconnect. A job whose `enqueued_at` is older than `job_ttl_seconds` is **dropped unsent** with a warning — a credential email nobody delivered in time must not be delivered late. Otherwise `provider.send` runs in a worker thread; success completes the job, and an `EmailProviderError` **leaves it inflight on purpose** so the reaper retries it after the stale threshold. Returns whether a delivery attempt was made, which is what the pace applies to. |
| `_dequeue_loop(...)` | Pause check → `dequeue_mail_job_id` → `publish_worker_last_job` → `process_job`, then rest `60 / NOCA_MAILER_MAX_PER_MINUTE` seconds after every attempt (interruptible on shutdown). This is the deployment-wide provider budget: no burst of requests can exceed it. |
| `run_mailer_worker()` | Boots the provider, PostgreSQL (pause-state reconciliation only), the Valkey runtime, the heartbeat file, signal handling, and the pause machinery when `NOCA_WORKER_COMMAND_SECRET` is set; then gathers the dequeue loop, the reaper, the heartbeat loop, the worker-presence loop, and the command loop. Shutdown marks the worker offline and prunes stale presence records. |

## `reaper.py`

| Function | Description |
|---|---|
| `run_reaper_loop(valkey_runtime, stop_event, logger, stale_threshold_s, reaper_interval_s, max_requeue_count)` | Every `reaper_interval_s`, scans `mail:queue:inflight:times` for jobs inflight longer than `stale_threshold_s` — a crash mid-delivery, or a provider refusal the worker deliberately left behind, so the threshold doubles as the retry delay. |
| `handle_stale_job(valkey_runtime, job_id, max_requeue_count, logger) -> str` | One atomic Valkey script (`requeue_stale_mail_job`): requeues the job with `requeue_count + 1` only while it is still inflight and its hash still exists (`requeued`), drops it past `max_requeue_count` (`dropped`), cleans an expired one (`expired`) or leaves a completed one alone (`not_inflight`). The hash keeps its original TTL, so a retry never extends how long a credential sits at rest; `unavailable` is retried next scan. |

## `config.py`

`Settings` reads the same `NOCA_`-prefixed `.env` as every module. The
provider block (`NOCA_SEND_EMAIL`, `NOCA_EMAIL_PROVIDER`, `NOCA_SMTP_*`,
`NOCA_EMAIL_MBOX_LOG_DIR`) exists **only here** — Web and Arena carry none of
it — and its validators (absolute mbox path, SMTP fields required for real
sending) moved here with it. `NOCA_EMAIL_SENDER` / `NOCA_EMAIL_QUEUE_JOB_TTL_SECONDS`
are shared with the producers; the worker-specific knobs live under
`NOCA_MAILER_*` (see [CONFIG.md](../../docs/CONFIG.md), "Mailer worker"). The
budget knobs are read only to satisfy the shared `EmailSettings` contract:
budgets are charged at enqueue, never here.

## `healthcheck.py`

`heartbeat_is_healthy()` / `main()` — the container healthcheck
(`python -m mailer.healthcheck`): healthy while `NOCA_MAILER_HEARTBEAT_FILE` is
fresher than `NOCA_MAILER_HEARTBEAT_STALE_SECONDS`, unhealthy on any failure
including an unloadable configuration.

## `database.py`

`create_engine(db_url)` — a two-connection pool used only by the pause-state
reconciliation (`arena_worker_pause_state`); the worker never reads or writes
application tables.

## Operational notes

- **Required by Web and Arena at startup.** Both call `wait_for_mailer` once their
  Valkey runtime is up and fail after `NOCA_STARTUP_TIMEOUT_SECONDS` without a live
  mailer, since they cannot send on their own. Only at startup: a later restart of
  the mailer refuses no request, the queue simply waits.
- **Single replica.** The pace is per process; two replicas would double the
  provider rate.
- **Pausable** from the Arena admin dashboard like autojudge and aiassistant:
  pause state is authoritative in PostgreSQL, the Valkey command is only a
  nudge. A paused mailer lets the queue grow (the dashboard card shows the
  count) and drains it on resume at the configured pace.
- **Health monitor.** The worker publishes `WorkerClass.MAILER` presence, so it
  appears on the health monitor dashboard as *Mailer*.
- **The mbox audit copy knows the queue.** With `NOCA_EMAIL_MBOX_LOG_DIR` set on
  the worker, each delivered copy carries `X-NOCA-Queued-At`, `X-NOCA-Queue-Seconds`
  and `X-NOCA-Delivery-Attempt` (from the job's `enqueued_at` and `requeue_count`),
  so a slow or retried delivery can be read off the log later.
- **Nothing at rest survives the TTL.** Every job hash expires on its own after
  `NOCA_EMAIL_QUEUE_JOB_TTL_SECONDS`, and the worker refuses to deliver an older
  one, so a stopped mailer never delivers stale credentials when it comes back.
