#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest task service package."""

from .errors import (
    ContestNotRunningError,
    DuplicatePrintTaskError,
    ForbiddenTaskActionError,
    PrintRequestsDisabledError,
    TaskAcquisitionTimeoutError,
    TaskAlreadyAcquiredError,
    TaskAlreadyFinishedError,
    TaskError,
    TaskLockUnavailableError,
    TaskNotAcquiredByActorError,
)
from .lifecycle import (
    acquire_task,
    create_balloon_task,
    create_print_task,
    create_sos_task,
    finish_task,
    release_task,
)
from .queries import get_task, list_tasks
from .views import TaskView

__all__ = [
    "ContestNotRunningError",
    "DuplicatePrintTaskError",
    "ForbiddenTaskActionError",
    "PrintRequestsDisabledError",
    "TaskAcquisitionTimeoutError",
    "TaskAlreadyAcquiredError",
    "TaskAlreadyFinishedError",
    "TaskError",
    "TaskLockUnavailableError",
    "TaskNotAcquiredByActorError",
    "TaskView",
    "acquire_task",
    "create_balloon_task",
    "create_print_task",
    "create_sos_task",
    "finish_task",
    "get_task",
    "list_tasks",
    "release_task",
]
