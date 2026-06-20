#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Shared SQLAlchemy Core schema for the entire application.

This module is intentionally ORM-free. It is the structural source of truth for
the physical database schema used by both the web application and the autojudge
worker. Web models map ORM classes onto these tables; autojudge uses the same
tables directly via SQLAlchemy Core.
"""

from ._base import metadata
from .arena import (
    arena_affiliation_rating_history,
    arena_ai_batch_jobs,
    arena_ai_credit_transactions,
    arena_backup_2fa,
    arena_login_history,
    arena_notifications,
    arena_problem_rating_history,
    arena_problem_ratings,
    arena_problem_solvers,
    arena_problem_tried,
    arena_problems,
    arena_submission_ai_reviews,
    arena_submission_judgments,
    arena_submission_teacher_feedback,
    arena_submission_test_results,
    arena_submissions,
    arena_test_cases,
    arena_user_rating_history,
    arena_users,
    arena_worker_command_audit,
    arena_worker_pause_state,
)
from .clarification import clarifications
from .contest import contest_languages, contests, sites, tasks
from .language import languages
from .problem import (
    problem_categories,
    problem_categories_map,
    problem_language_limits,
    problems,
    profiling_case_results,
    profiling_runs,
    test_cases,
)
from .problem_limits import (
    problem_limit_change_batch_languages,
    problem_limit_change_batch_submissions,
    problem_limit_change_batches,
)
from .submission import (
    human_submission_confirmations,
    submission_judgment_audit,
    submission_judgments,
    submission_test_results,
    submissions,
    verdict_overrides,
)
from .users import login_history, uber_admins, users

__all__ = [
    "metadata",
    "arena_affiliation_rating_history",
    "arena_ai_batch_jobs",
    "arena_ai_credit_transactions",
    "arena_backup_2fa",
    "arena_login_history",
    "arena_notifications",
    "arena_problem_rating_history",
    "arena_problem_ratings",
    "arena_problem_solvers",
    "arena_problem_tried",
    "arena_problems",
    "arena_submission_ai_reviews",
    "arena_submission_judgments",
    "arena_submission_teacher_feedback",
    "arena_submission_test_results",
    "arena_submissions",
    "arena_test_cases",
    "arena_user_rating_history",
    "arena_users",
    "arena_worker_command_audit",
    "arena_worker_pause_state",
    "clarifications",
    "contest_languages",
    "contests",
    "human_submission_confirmations",
    "languages",
    "login_history",
    "problem_categories",
    "problem_categories_map",
    "problem_language_limits",
    "problem_limit_change_batch_languages",
    "problem_limit_change_batch_submissions",
    "problem_limit_change_batches",
    "profiling_case_results",
    "profiling_runs",
    "problems",
    "sites",
    "submission_judgment_audit",
    "submission_judgments",
    "submission_test_results",
    "submissions",
    "tasks",
    "test_cases",
    "uber_admins",
    "users",
    "verdict_overrides",
]
