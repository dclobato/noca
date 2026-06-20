#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest judging service package."""

from .chief_judge import (
    ChiefJudgeAdminPanel,
    get_chief_judge_admin_panel,
    list_contest_judges,
    remove_chief_judge,
    set_chief_judge,
)
from .errors import (
    AlreadyConfirmedError,
    ChiefJudgeRemovalBlockedError,
    JudgingServiceError,
    JudgmentNotDoneError,
    JudgmentNotReadyError,
    NoFinalVerdictError,
    ReviewAcquisitionTimeoutError,
    ReviewAlreadyLockedError,
    ReviewLockUnavailableError,
    ReviewNotHeldByActorError,
    SameVerdictError,
)
from .history import get_judging_history
from .rejudge import (
    create_balloon_task_if_needed,
    queue_limit_change_batch_rejudges,
    rejudge_submission,
)
from .review import acquire_submission_review, confirm_verdict, release_submission_review
from .types import (
    ContestSetChiefJudgeRequest,
    JudgingHistoryEntry,
    JudgingHistoryResponse,
    VerdictOverrideRequest,
    VerdictOverrideResponse,
)
from .verdicts import override_verdict

__all__ = [
    "AlreadyConfirmedError",
    "ChiefJudgeAdminPanel",
    "ChiefJudgeRemovalBlockedError",
    "ContestSetChiefJudgeRequest",
    "JudgingHistoryEntry",
    "JudgingHistoryResponse",
    "JudgingServiceError",
    "JudgmentNotDoneError",
    "JudgmentNotReadyError",
    "NoFinalVerdictError",
    "ReviewAcquisitionTimeoutError",
    "ReviewAlreadyLockedError",
    "ReviewLockUnavailableError",
    "ReviewNotHeldByActorError",
    "SameVerdictError",
    "VerdictOverrideRequest",
    "VerdictOverrideResponse",
    "acquire_submission_review",
    "confirm_verdict",
    "create_balloon_task_if_needed",
    "get_chief_judge_admin_panel",
    "get_judging_history",
    "list_contest_judges",
    "override_verdict",
    "queue_limit_change_batch_rejudges",
    "rejudge_submission",
    "release_submission_review",
    "remove_chief_judge",
    "set_chief_judge",
]
