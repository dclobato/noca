#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Clarification service exceptions."""

from __future__ import annotations

import datetime


class ClarificationError(Exception):
    """Base class for all clarification service errors."""


class ContestNotRunningError(ClarificationError):
    """Raised when a state-changing action is attempted outside the contest window."""


class ForbiddenClarificationActionError(ClarificationError):
    """Raised when the actor's role or contest membership is insufficient for the action."""


class ClarificationAlreadyAnsweredError(ClarificationError):
    """Raised when an answer or acquire is attempted on an already-answered clarification."""


class ClarificationAlreadyAcquiredError(ClarificationError):
    """Raised when a second judge tries to acquire an already-locked clarification."""


class ClarificationLockUnavailableError(ClarificationError):
    """Raised when the Valkey-backed clarification lock service is unavailable."""


class ClarificationNotAcquiredByActorError(ClarificationError):
    """Raised when a judge tries to release or answer a lock they do not hold."""


class ClarificationHiddenError(ClarificationError):
    """Raised when an answer or acquire is attempted on a hidden clarification."""


class ClarificationAcquisitionTimeoutError(ClarificationError):
    """Raised when the judge's acquisition window has expired."""

    def __init__(self, message: str, *, acquired_at: datetime.datetime) -> None:
        super().__init__(message)
        self.acquired_at = acquired_at
