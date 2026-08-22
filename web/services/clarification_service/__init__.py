#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
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
    mark_clarification_answers_read,
    release_clarification,
    toggle_hidden_clarification,
)
from .permissions import (
    can_answer_clarifications,
    can_create_announcement,
    can_force_release_clarifications,
    can_request_clarification,
)
from .queries import (
    ClarificationSort,
    count_pending_clarifications,
    count_unread_clarification_answers,
    get_clarification,
    list_clarifications,
    normalize_clarification_sort,
)
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
    "can_create_announcement",
    "can_force_release_clarifications",
    "can_request_clarification",
    "count_pending_clarifications",
    "count_unread_clarification_answers",
    "create_announcement",
    "create_clarification",
    "get_clarification",
    "list_clarifications",
    "mark_clarification_answers_read",
    "normalize_clarification_sort",
    "release_clarification",
    "toggle_hidden_clarification",
]
