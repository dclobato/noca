#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Task service exceptions."""

from __future__ import annotations

import datetime


class TaskError(Exception):
    """Base class for all task service errors."""


class ContestNotRunningError(TaskError):
    """Raised when a state-changing action is attempted outside the contest window."""


class ForbiddenTaskActionError(TaskError):
    """Raised when the actor's role or contest membership is insufficient for the action."""


class TaskAlreadyAcquiredError(TaskError):
    """Raised when a second staff member tries to acquire an already-locked task."""


class TaskLockUnavailableError(TaskError):
    """Raised when the Valkey-backed task lock service is unavailable."""


class TaskAlreadyFinishedError(TaskError):
    """Raised when an acquire or finish is attempted on an already-finished task."""


class TaskNotAcquiredByActorError(TaskError):
    """Raised when a staff member tries to release or finish a lock they do not hold."""


class TaskAcquisitionTimeoutError(TaskError):
    """Raised when the staff member's acquisition window has expired."""

    def __init__(self, message: str, *, acquired_at: datetime.datetime) -> None:
        super().__init__(message)
        self.acquired_at = acquired_at


class DuplicatePrintTaskError(TaskError):
    """Raised when a duplicate unfinished PRINT task already exists."""


class PrintRequestsDisabledError(TaskError):
    """Raised when the contest has disabled team-created PRINT requests."""
