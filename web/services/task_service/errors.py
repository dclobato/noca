#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
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


class TaskRateLimitError(TaskError):
    """Raised when a team exceeds the per-window SOS or PRINT task budget."""

    def __init__(self, message: str, *, next_allowed_at: datetime.datetime) -> None:
        """Initialize with the earliest time the team may create another task.

        Args:
            message: Human-readable reason.
            next_allowed_at: When the oldest in-window task leaves the window.
        """
        super().__init__(message)
        self.next_allowed_at = next_allowed_at


class OpenSosTaskLimitError(TaskError):
    """Raised when a team already holds the maximum number of unfinished SOS tasks."""

    def __init__(self, message: str, *, open_count: int, limit: int) -> None:
        """Initialize with the team's open SOS count and the configured ceiling.

        Args:
            message: Human-readable reason.
            open_count: Unfinished SOS tasks the team currently holds.
            limit: Configured maximum.
        """
        super().__init__(message)
        self.open_count = open_count
        self.limit = limit
