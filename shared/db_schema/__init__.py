#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
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
from .announcement import announcements, arena_announcement_acknowledgments
from .arena import (
    arena_affiliation_rating_history,
    arena_ai_batch_jobs,
    arena_ai_credit_transactions,
    arena_backup_2fa,
    arena_badge_cycle_state,
    arena_login_history,
    arena_notifications,
    arena_problem_custom_validators,
    arena_problem_rating_history,
    arena_problem_ratings,
    arena_problem_set_student_feedback,
    arena_problem_solvers,
    arena_problem_tried,
    arena_problems,
    arena_rating_cycle_state,
    arena_sample_interactions,
    arena_submission_ai_reviews,
    arena_submission_interactive_attempts,
    arena_submission_judgments,
    arena_submission_teacher_feedback,
    arena_submission_test_results,
    arena_submissions,
    arena_test_cases,
    arena_throttle_secret_versions,
    arena_user_badges,
    arena_user_google_identities,
    arena_user_rating_history,
    arena_user_reputation,
    arena_user_throttle_hashes,
    arena_users,
    arena_worker_command_audit,
    arena_worker_pause_state,
)
from .clarification import clarification_reads, clarifications
from .contest import contest_languages, contests, site_secrets, sites, tasks
from .language import languages
from .problem import (
    problem_categories,
    problem_categories_map,
    problem_custom_validators,
    problem_language_limits,
    problem_sample_interactions,
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
from .security_events import security_events
from .solution_test import solution_test_case_results, solution_test_runs
from .submission import (
    human_submission_confirmations,
    submission_interactive_attempts,
    submission_judgment_audit,
    submission_judgments,
    submission_test_results,
    submissions,
    verdict_overrides,
)
from .users import login_history, uber_admins, users, users_media

__all__ = [
    "metadata",
    "arena_affiliation_rating_history",
    "arena_ai_batch_jobs",
    "arena_ai_credit_transactions",
    "arena_badge_cycle_state",
    "arena_backup_2fa",
    "arena_login_history",
    "arena_notifications",
    "arena_problem_rating_history",
    "arena_problem_custom_validators",
    "arena_problem_ratings",
    "arena_problem_solvers",
    "arena_problem_tried",
    "arena_problem_set_student_feedback",
    "arena_problems",
    "arena_rating_cycle_state",
    "arena_submission_ai_reviews",
    "arena_submission_judgments",
    "arena_submission_interactive_attempts",
    "arena_submission_teacher_feedback",
    "arena_submission_test_results",
    "arena_sample_interactions",
    "arena_submissions",
    "arena_test_cases",
    "arena_user_badges",
    "arena_user_google_identities",
    "arena_user_rating_history",
    "arena_user_reputation",
    "arena_throttle_secret_versions",
    "arena_user_throttle_hashes",
    "arena_users",
    "arena_worker_command_audit",
    "arena_worker_pause_state",
    "announcements",
    "arena_announcement_acknowledgments",
    "clarification_reads",
    "clarifications",
    "contest_languages",
    "contests",
    "human_submission_confirmations",
    "languages",
    "login_history",
    "problem_categories",
    "problem_categories_map",
    "problem_language_limits",
    "problem_custom_validators",
    "problem_limit_change_batch_languages",
    "problem_limit_change_batch_submissions",
    "problem_limit_change_batches",
    "problem_sample_interactions",
    "profiling_case_results",
    "profiling_runs",
    "security_events",
    "problems",
    "site_secrets",
    "sites",
    "solution_test_case_results",
    "solution_test_runs",
    "submission_judgment_audit",
    "submission_judgments",
    "submission_interactive_attempts",
    "submission_test_results",
    "submissions",
    "tasks",
    "test_cases",
    "uber_admins",
    "users",
    "users_media",
    "verdict_overrides",
]
