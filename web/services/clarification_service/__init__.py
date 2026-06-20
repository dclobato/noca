#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest clarification service package."""

from .errors import (
    ClarificationAcquisitionTimeoutError,
    ClarificationAlreadyAcquiredError,
    ClarificationAlreadyAnsweredError,
    ClarificationError,
    ClarificationHiddenError,
    ClarificationLockUnavailableError,
    ClarificationNotAcquiredByActorError,
    ContestNotRunningError,
    ForbiddenClarificationActionError,
)
from .lifecycle import (
    acquire_clarification,
    answer_clarification,
    create_announcement,
    create_clarification,
    release_clarification,
    toggle_hidden_clarification,
)
from .queries import get_clarification, list_clarifications
from .views import ClarificationView

__all__ = [
    "ClarificationAcquisitionTimeoutError",
    "ClarificationAlreadyAcquiredError",
    "ClarificationAlreadyAnsweredError",
    "ClarificationError",
    "ClarificationHiddenError",
    "ClarificationLockUnavailableError",
    "ClarificationNotAcquiredByActorError",
    "ClarificationView",
    "ContestNotRunningError",
    "ForbiddenClarificationActionError",
    "acquire_clarification",
    "answer_clarification",
    "create_announcement",
    "create_clarification",
    "get_clarification",
    "list_clarifications",
    "release_clarification",
    "toggle_hidden_clarification",
]
