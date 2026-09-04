#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Registry of the NOCA runtime services watched by the health monitor."""

from dataclasses import dataclass

from shared.services.valkey_service import WorkerClass


@dataclass(frozen=True, slots=True)
class MonitoredService:
    """Presentation metadata for one monitored runtime service."""

    worker_class: WorkerClass
    title: str
    icon: str


#: Display order of the monitored services on the dashboard. Icons follow
#: the Arena admin dashboard metadata for the worker classes it also shows.
MONITORED_SERVICES: tuple[MonitoredService, ...] = (
    MonitoredService(WorkerClass.WEB, "Contest", "language"),
    MonitoredService(WorkerClass.ARENA, "Arena", "stadium"),
    MonitoredService(WorkerClass.AUTOJUDGE, "AutoJudge", "gavel"),
    MonitoredService(WorkerClass.RATING, "Rating", "monitoring"),
    MonitoredService(WorkerClass.AIASSISTANT, "AI Assistant", "smart_toy"),
    MonitoredService(WorkerClass.ANIMATOR, "Animator", "slideshow"),
    MonitoredService(WorkerClass.MAILER, "Mailer", "outgoing_mail"),
)
