#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""SQLAlchemy Core schema definitions for the Arena module."""

from .arena_ai_batch_jobs import arena_ai_batch_jobs
from .arena_ai_credit_transactions import arena_ai_credit_transactions
from .arena_classes import (
    arena_class_memberships,
    arena_class_registration_requests,
    arena_classes,
    arena_problem_set_problems,
    arena_problem_sets,
)
from .arena_heatmap import arena_user_submission_heatmap
from .arena_notifications import arena_notifications
from .arena_problem_favorites import arena_problem_favorites
from .arena_problem_set_snapshots import (
    arena_problem_set_problem_snapshots,
    arena_problem_set_user_snapshots,
)
from .arena_problems import (
    arena_problem_categories,
    arena_problem_category_map,
    arena_problem_ratings,
    arena_problems,
    arena_test_cases,
)
from .arena_rating_history import (
    arena_affiliation_rating_history,
    arena_problem_rating_history,
    arena_user_rating_history,
)
from .arena_statistics import arena_problem_statistics
from .arena_submissions import (
    arena_problem_solvers,
    arena_problem_tried,
    arena_submission_ai_reviews,
    arena_submission_judgments,
    arena_submission_teacher_feedback,
    arena_submission_test_results,
    arena_submissions,
)
from .arena_users import arena_affiliations, arena_backup_2fa, arena_login_history, arena_users
from .arena_worker_control import arena_worker_command_audit, arena_worker_pause_state

__all__ = [
    "arena_affiliation_rating_history",
    "arena_ai_batch_jobs",
    "arena_ai_credit_transactions",
    "arena_affiliations",
    "arena_class_memberships",
    "arena_class_registration_requests",
    "arena_classes",
    "arena_problem_favorites",
    "arena_problem_set_problem_snapshots",
    "arena_problem_set_problems",
    "arena_problem_set_user_snapshots",
    "arena_problem_sets",
    "arena_backup_2fa",
    "arena_login_history",
    "arena_notifications",
    "arena_problem_categories",
    "arena_problem_category_map",
    "arena_problem_rating_history",
    "arena_problem_ratings",
    "arena_problem_solvers",
    "arena_problem_statistics",
    "arena_problem_tried",
    "arena_problems",
    "arena_submission_ai_reviews",
    "arena_submission_judgments",
    "arena_submission_teacher_feedback",
    "arena_submission_test_results",
    "arena_submissions",
    "arena_test_cases",
    "arena_user_rating_history",
    "arena_user_submission_heatmap",
    "arena_users",
    "arena_worker_command_audit",
    "arena_worker_pause_state",
]
