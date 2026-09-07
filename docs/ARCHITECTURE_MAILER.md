# NOCA Mailer Module Architecture

This document describes the `mailer/` module: the single-replica worker that
drains the Valkey mail queue the Web and Arena processes fill and is the only
NOCA process that talks to an email provider. It covers what the worker owns
and how the queue, the sending rate, retries, and stale jobs behave. Read
[ARCHITECTURE.md](ARCHITECTURE.md) first for why outbound email crosses the
infrastructure boundary at all.

Related references:
- [ARCHITECTURE.md](ARCHITECTURE.md) for the system overview and the mail-queue boundary
- [mailer/docs/SERVICES.md](../mailer/docs/SERVICES.md) for the worker's services
- [SHARED_SERVICES.md](SHARED_SERVICES.md) for `email_service.py`, `email_budget.py`, and the Valkey queue contract

## Responsibilities

The mailer module is a standalone single-replica async worker and the only NOCA
process that talks to the email provider. Every outbound email is rendered by
the Web or Arena process that decided to send it and handed, fully formed, to
the shared `EmailService`, which charges the acting user's per-actor budget
and pushes a `MailJob` onto the Valkey mail queue (`mail:queue:pending`,
inflight list, dispatch-time ZSET, and a TTL'd `mail:job:{id}` hash). Whether
that job is really sent, and through what (`NOCA_SEND_EMAIL`,
`NOCA_EMAIL_PROVIDER`, `NOCA_SMTP_*`), is the worker's decision alone. It
dequeues one job at a time -- the move to inflight and its dispatch timestamp
are one atomic script -- delivers it through the configured `EmailProvider` on a
worker thread, and rests `60 / NOCA_MAILER_MAX_PER_MINUTE` seconds after every
attempt — the deployment-wide sending rate, set in exactly one place. A provider
refusal leaves the job inflight on purpose; the reaper requeues it after the
stale threshold in one atomic step that keeps the hash's original TTL, bounded
by a requeue cap. A job older than `NOCA_EMAIL_QUEUE_JOB_TTL_SECONDS` is dropped
unsent, and its hash expires on its own, so a stopped mailer never delivers a
stale credential when it comes back; a job the worker cannot read during a
Valkey outage is left inflight rather than discarded. It has no templates, reads PostgreSQL only to reconcile its pause state,
is pausable from the Arena admin dashboard like autojudge and aiassistant, and
appears on the health monitor as *Mailer*. See
[mailer/docs/SERVICES.md](../mailer/docs/SERVICES.md) and the `email_service.py`,
`email_budget.py`, and `valkey_service/` entries in
[SHARED_SERVICES.md](SHARED_SERVICES.md).
