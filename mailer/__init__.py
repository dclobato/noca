#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""NOCA mailer worker: the one process that talks to the email provider.

The Web and Arena HTTP processes render every outbound email and push it,
fully formed, onto the Valkey mail queue (``mail:queue:pending``). This worker
drains that queue at the deployment's own pace and delivers each message
through the shared ``EmailProvider``. It runs several independent ``asyncio``
loops:

- ``_dequeue_loop`` -- pops one job at a time, delivers it, and paces itself to
  ``NOCA_MAILER_MAX_PER_MINUTE`` (the deployment-wide provider budget);
- ``run_reaper_loop`` (``mailer.reaper``) -- requeues jobs left inflight by a
  crash or a provider failure, bounded by ``NOCA_MAILER_MAX_REQUEUE_COUNT``;
- ``heartbeat_loop`` / ``worker_presence_loop`` -- container health and the
  Arena dashboard / health monitor presence;
- ``worker_command_loop`` -- pause/resume nudges when
  ``NOCA_WORKER_COMMAND_SECRET`` is set (pause state itself lives in PostgreSQL).

The worker has no templates and no business logic: a job is delivered as it
was rendered, or dropped with a warning once it is older than its TTL, since a
credential email nobody delivered in time must not be delivered late.
"""
