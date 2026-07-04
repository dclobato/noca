#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena ORM model exports."""

from .arena_affiliations import ArenaAffiliation
from .arena_ai_credit_transactions import ArenaAiCreditTransaction
from .arena_auth_records import ArenaBackup2FA, ArenaLoginHistory
from .arena_badges import ArenaUserBadge
from .arena_classes import (
    ArenaClass,
    ArenaClassMembership,
    ArenaClassRegistrationRequest,
)
from .arena_notifications import ArenaNotification
from .arena_problem_set_snapshots import (
    ArenaProblemSetProblemSnapshot,
    ArenaProblemSetUserSnapshot,
)
from .arena_problem_sets import ArenaProblemSet
from .arena_problems import ArenaCategory, ArenaProblem, ArenaRatingProblem, ArenaTestCase
from .arena_submissions import (
    ArenaSubmission,
    ArenaSubmissionAIReview,
    ArenaSubmissionJudgment,
    ArenaSubmissionTeacherFeedback,
    ArenaSubmissionTestResult,
    ArenaUserSolvedProblem,
    ArenaUserTriedProblem,
)
from .arena_users import ArenaUser

__all__ = [
    "ArenaAffiliation",
    "ArenaAiCreditTransaction",
    "ArenaBackup2FA",
    "ArenaClass",
    "ArenaClassMembership",
    "ArenaClassRegistrationRequest",
    "ArenaLoginHistory",
    "ArenaNotification",
    "ArenaProblemSet",
    "ArenaProblemSetProblemSnapshot",
    "ArenaProblemSetUserSnapshot",
    "ArenaCategory",
    "ArenaProblem",
    "ArenaRatingProblem",
    "ArenaSubmission",
    "ArenaSubmissionAIReview",
    "ArenaSubmissionJudgment",
    "ArenaSubmissionTeacherFeedback",
    "ArenaSubmissionTestResult",
    "ArenaTestCase",
    "ArenaUser",
    "ArenaUserBadge",
    "ArenaUserSolvedProblem",
    "ArenaUserTriedProblem",
]
