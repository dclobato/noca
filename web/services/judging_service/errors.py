#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Judging service exceptions."""


class JudgingServiceError(Exception):
    """Base class for judging service errors."""


class JudgmentNotDoneError(JudgingServiceError):
    """Raised when the active judgment is not finished."""


class SameVerdictError(JudgingServiceError):
    """Raised when an override repeats the same verdict."""


class ChiefJudgeRemovalBlockedError(JudgingServiceError):
    """Raised when a chief judge cannot be removed due to existing overrides."""


class AlreadyConfirmedError(JudgingServiceError):
    """Raised when the judge already confirmed this judgment."""


class JudgmentNotReadyError(JudgingServiceError):
    """Raised when a judgment is not ready for review."""


class NoFinalVerdictError(JudgingServiceError):
    """Raised when attempting to rejudge a submission that has no final verdict yet."""


class ReviewAlreadyLockedError(JudgingServiceError):
    """Raised when another judge already holds the review lock."""


class ReviewLockUnavailableError(JudgingServiceError):
    """Raised when the review lock backend is unavailable."""


class ReviewNotHeldByActorError(JudgingServiceError):
    """Raised when the caller does not hold the required review lock."""


class ReviewAcquisitionTimeoutError(JudgingServiceError):
    """Raised when the review acquisition window has expired."""
