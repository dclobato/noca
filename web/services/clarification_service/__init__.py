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
from .permissions import can_answer_clarifications, can_force_release_clarifications
from .queries import ClarificationSort, get_clarification, list_clarifications, normalize_clarification_sort
from .views import ClarificationView

__all__ = [
    "ClarificationAcquisitionTimeoutError",
    "ClarificationAlreadyAcquiredError",
    "ClarificationAlreadyAnsweredError",
    "ClarificationError",
    "ClarificationHiddenError",
    "ClarificationLockUnavailableError",
    "ClarificationNotAcquiredByActorError",
    "ClarificationSort",
    "ClarificationView",
    "ContestNotRunningError",
    "ForbiddenClarificationActionError",
    "acquire_clarification",
    "answer_clarification",
    "can_answer_clarifications",
    "can_force_release_clarifications",
    "create_announcement",
    "create_clarification",
    "get_clarification",
    "list_clarifications",
    "normalize_clarification_sort",
    "release_clarification",
    "toggle_hidden_clarification",
]
