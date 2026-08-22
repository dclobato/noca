#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reconcile database column comments with shared schema metadata.

Revision ID: 202608210001
Revises: 202608200002
Create Date: 2026-08-21
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "202608210001"
down_revision: str | Sequence[str] | None = "202608200002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CommentChange = tuple[str, str, str | None, str | None]

_COMMENT_CHANGES: tuple[CommentChange, ...] = (
    (
        "arena_ai_batch_jobs",
        "openai_batch_id",
        "OpenAI batch identifier, e.g. 'batch_xxx'. Returned by batches.create().",
        "OpenAI batch identifier, e.g. 'batch_xxx'. Returned by batches.create(). "
        "NULL while local_status='staged' (not yet submitted to OpenAI). Multiple rows may "
        "share the same value when bundled in one window batch.",
    ),
    (
        "arena_ai_batch_jobs",
        "local_status",
        "Local state machine: preparing, submitted, polling, completed, failed, expired, "
        "cancelled. See ArenaAIBatchJobStatus enum.",
        "Local state machine: staged, preparing, submitted, polling, completed, failed, "
        "expired, cancelled. See ArenaAIBatchJobStatus enum.",
    ),
    (
        "arena_ai_batch_jobs",
        "submitted_at",
        None,
        "Timestamp when the row was submitted to OpenAI (openai_batch_id assigned). Drives "
        "stale-batch detection; NULL while local_status='staged'.",
    ),
    (
        "arena_ai_credit_transactions",
        "amount",
        "Credit delta: positive for top-up, negative for consumption (typically -1).",
        "Credit delta: positive for top-up and refund (+1), negative for consumption (typically -1).",
    ),
    (
        "arena_ai_credit_transactions",
        "transaction_type",
        "'topup' when an admin adds credits; 'consumption' when a review consumes one.",
        "'topup' when an admin adds credits; 'consumption' when a review consumes one; "
        "'refund' when a stale platform batch review is expired and its credit returned.",
    ),
    (
        "arena_ai_credit_transactions",
        "submission_id",
        "FK to arena_submissions. Set only for consumption transactions.",
        "FK to arena_submissions. Set for consumption and refund transactions.",
    ),
    ("arena_login_history", "id", None, "Sequential login event identifier"),
    (
        "arena_login_history",
        "source_port",
        None,
        "Client source port associated with the IP address",
    ),
    (
        "arena_problems",
        "owner_id",
        None,
        "User responsible for creating and managing the problem.",
    ),
    (
        "arena_problems",
        "author",
        None,
        "Free-text author name when the owner is not the problem author.",
    ),
    (
        "arena_problems",
        "author_is_owner",
        None,
        "Whether the problem author is resolved from the owner's fullname.",
    ),
    ("arena_problems", "editorial", None, "Optional editor-only Markdown solution guide."),
    (
        "arena_problems",
        "editorial_release_policy",
        "When the editorial is released to participants. Not yet enforced anywhere.",
        "When the editorial is released to participants; not yet enforced.",
    ),
    (
        "arena_problems",
        "license",
        None,
        "Optional license information displayed on the public problem page.",
    ),
    (
        "arena_problems",
        "validator_type",
        "Stored, immutable validation strategy. Never inferred from validator source presence.",
        "Stored, immutable validation strategy; never inferred from validator source.",
    ),
    (
        "arena_problems",
        "artifact_generation",
        "Monotonic fence incremented by each editor save that promotes filesystem artifacts.",
        "Monotonic fence bumped by each editor save that promotes artifacts.",
    ),
    (
        "arena_submission_interactive_attempts",
        "transcript",
        None,
        "Ordered protocol conversation: {'lines': [{'dir', 'line', 'partial'?}], 'truncated': bool}.",
    ),
    (
        "arena_test_cases",
        "input_size_bytes",
        None,
        "On-disk byte size of the normalized (LF) input file; null until backfilled.",
    ),
    (
        "arena_test_cases",
        "output_size_bytes",
        None,
        "On-disk byte size of the normalized (LF) output file; null until backfilled.",
    ),
    (
        "arena_user_reputation",
        "user_id",
        None,
        "1:1 FK to arena_users. UNIQUE enforces at most one reputation snapshot per user.",
    ),
    (
        "arena_user_reputation",
        "signup_ip",
        None,
        "Client IP captured at signup. NULL for pre-existing users backfilled after the fact "
        "(the signup IP cannot be recovered).",
    ),
    (
        "arena_user_reputation",
        "ip_fraud_score",
        None,
        "IPQualityScore fraud_score (0-100) for the signup IP; NULL when unavailable.",
    ),
    (
        "arena_user_reputation",
        "ip_report",
        None,
        "Full IPReputation dict from IPQualityScore; NULL when the lookup was unavailable.",
    ),
    (
        "arena_user_reputation",
        "ip_checked_at",
        None,
        "Timestamp of the IP reputation lookup; NULL when never checked.",
    ),
    (
        "arena_user_reputation",
        "email_fraud_score",
        None,
        "IPQualityScore email fraud_score (0-100); NULL when unavailable.",
    ),
    (
        "arena_user_reputation",
        "email_overall_score",
        None,
        "IPQualityScore email overall_score (0-4) validity confidence; NULL when unavailable.",
    ),
    (
        "arena_user_reputation",
        "email_report",
        None,
        "Full EmailReputation dict from IPQualityScore; NULL when the lookup was unavailable.",
    ),
    (
        "arena_user_reputation",
        "email_checked_at",
        None,
        "Timestamp of the email reputation lookup; NULL when never checked.",
    ),
    (
        "arena_user_submission_heatmap",
        "user_id",
        None,
        "FK to arena_users. CASCADE so orphans are removed automatically.",
    ),
    (
        "arena_user_submission_heatmap",
        "data",
        None,
        "Array of [YYYY-MM-DD, count] pairs for days with at least one submission.",
    ),
    (
        "arena_user_submission_heatmap",
        "range_start",
        None,
        "YYYY-MM-DD UTC date of the first day in the 52-week window.",
    ),
    (
        "arena_user_submission_heatmap",
        "range_end",
        None,
        "YYYY-MM-DD UTC date of the last day in the window (the computation date).",
    ),
    ("login_history", "id", None, "Sequential login event identifier"),
    ("problems", "editorial", None, "Optional editor-only Markdown solution guide."),
    (
        "problems",
        "validator_type",
        "Stored, immutable validation strategy. Never inferred from validator source presence.",
        "Stored, immutable validation strategy; never inferred from validator source.",
    ),
    (
        "problems",
        "artifact_generation",
        "Monotonic fence incremented by each editor save that promotes filesystem artifacts.",
        "Monotonic fence bumped by each editor save that promotes artifacts.",
    ),
    (
        "solution_test_case_results",
        "stderr_excerpt",
        None,
        "Contestant-side excerpt; NULL for ordinary rows.",
    ),
    (
        "solution_test_case_results",
        "validator_exit_code",
        None,
        "Interactive rows only; NULL for ordinary rows.",
    ),
    (
        "solution_test_case_results",
        "validator_signal",
        None,
        "Interactive rows only; NULL for ordinary rows.",
    ),
    (
        "solution_test_case_results",
        "validator_stderr_excerpt",
        None,
        "Interactive rows only; NULL for ordinary rows.",
    ),
    (
        "solution_test_case_results",
        "limit_outcome",
        None,
        "Enforced MLE/OLE/TLE outcome for an interactive attempt; NULL otherwise.",
    ),
    (
        "solution_test_case_results",
        "validator_verdict",
        None,
        "Clean validator exit reading for an interactive attempt; mutually exclusive with crash_reason.",
    ),
    (
        "solution_test_case_results",
        "crash_reason",
        None,
        "Typed reason an interactive attempt did not obtain a clean validator exit.",
    ),
    (
        "submission_interactive_attempts",
        "transcript",
        None,
        "Ordered protocol conversation: {'lines': [{'dir', 'line', 'partial'?}], 'truncated': bool}.",
    ),
    (
        "users_media",
        "foto_base64",
        None,
        "Original user photo stored as a base64-encoded string",
    ),
    (
        "users_media",
        "avatar_base64",
        None,
        "Resized avatar derived from the user photo, stored as a base64-encoded string",
    ),
    ("users_media", "foto_mime", None, "Detected MIME type of the stored user photo"),
    ("users_media", "dta_foto", None, "Timestamp of the last user photo update"),
    ("users_media", "audio_base64", None, "User audio clip stored as a base64-encoded string"),
    ("users_media", "audio_mime", None, "Detected MIME type of the stored user audio clip"),
    ("users_media", "dta_audio", None, "Timestamp of the last user audio clip update"),
)


def _apply_comments(*, downgrade: bool) -> None:
    """Apply the authoritative or previous comment for every drifted column."""
    changes = reversed(_COMMENT_CHANGES) if downgrade else _COMMENT_CHANGES
    for table_name, column_name, old_comment, new_comment in changes:
        comment, existing_comment = (old_comment, new_comment) if downgrade else (new_comment, old_comment)
        op.alter_column(
            table_name,
            column_name,
            comment=comment,
            existing_comment=existing_comment,
        )


def upgrade() -> None:
    """Synchronize database comments with shared schema metadata."""
    _apply_comments(downgrade=False)


def downgrade() -> None:
    """Restore the comments present before this reconciliation."""
    _apply_comments(downgrade=True)
