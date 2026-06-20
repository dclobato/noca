"""add contest_languages table and drop legacy db lock columns

Revision ID: c7f2a9b4e1d3
Revises: 4a7e2f9c1b83
Create Date: 2026-04-05 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7f2a9b4e1d3"
down_revision: str | Sequence[str] | None = "4a7e2f9c1b83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_index_if_exists(table_name: str, index_name: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
    if index_name in existing_indexes:
        op.drop_index(index_name, table_name=table_name)


def _drop_foreign_key_if_exists(table_name: str, candidate_names: list[str]) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_fks = {fk["name"] for fk in inspector.get_foreign_keys(table_name) if fk.get("name")}
    for candidate_name in candidate_names:
        if candidate_name in existing_fks:
            op.drop_constraint(candidate_name, table_name, type_="foreignkey")
            return


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
    if column_name in existing_columns:
        op.drop_column(table_name, column_name)


def upgrade() -> None:
    """Upgrade schema."""
    # --- contest_languages junction table ---
    op.create_table(
        "contest_languages",
        sa.Column("contest_id", sa.String(length=36), nullable=False, comment="Contest this language is allowed for."),
        sa.Column("language_id", sa.String(length=64), nullable=False, comment="Language allowed in this contest."),
        sa.ForeignKeyConstraint(["contest_id"], ["contests.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["language_id"], ["languages.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("contest_id", "language_id"),
    )
    # Backfill existing contests with all currently active languages so they keep working after migration.
    conn = op.get_bind()
    contests = conn.execute(sa.text("SELECT id FROM contests")).fetchall()
    languages = conn.execute(sa.text("SELECT id FROM languages WHERE active = TRUE")).fetchall()
    rows = [{"contest_id": c[0], "language_id": la[0]} for c in contests for la in languages]
    if rows:
        conn.execute(
            sa.text("INSERT INTO contest_languages (contest_id, language_id) VALUES (:contest_id, :language_id)"),
            rows,
        )

    # --- drop legacy DB lock columns (replaced by Valkey TTL locks) ---
    _drop_column_if_exists("clarifications", "acquired_timestamp_seconds")
    _drop_column_if_exists("clarifications", "acquired_at")

    _drop_column_if_exists("tasks", "acquired_timestamp_seconds")
    _drop_column_if_exists("tasks", "acquired_at")

    _drop_index_if_exists("submission_judgments", "ix_submission_judgments_reviewer_id")
    _drop_foreign_key_if_exists(
        "submission_judgments",
        ["submission_judgments_reviewer_id_fkey", "fk_submission_judgments_reviewer_id"],
    )
    _drop_column_if_exists("submission_judgments", "review_acquired_at")
    _drop_column_if_exists("submission_judgments", "reviewer_id")


def downgrade() -> None:
    """Downgrade schema."""
    # --- restore legacy DB lock columns ---
    op.add_column(
        "submission_judgments",
        sa.Column(
            "reviewer_id",
            sa.String(length=36),
            nullable=True,
            comment="ID of the judge who currently holds the review lock",
        ),
    )
    op.add_column(
        "submission_judgments",
        sa.Column(
            "review_acquired_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Time when the review lock was acquired",
        ),
    )
    op.create_foreign_key(
        "submission_judgments_reviewer_id_fkey",
        "submission_judgments",
        "users",
        ["reviewer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_submission_judgments_reviewer_id", "submission_judgments", ["reviewer_id"], unique=False)

    op.add_column(
        "tasks",
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Time when the task was acquired by a staff to handle",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "acquired_timestamp_seconds",
            sa.Integer(),
            nullable=True,
            comment="Seconds since contest start when the task was acquired by a staff member.",
        ),
    )

    op.add_column(
        "clarifications",
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Time when the clarification was acquired by a judge for answering",
        ),
    )
    op.add_column(
        "clarifications",
        sa.Column(
            "acquired_timestamp_seconds",
            sa.Integer(),
            nullable=True,
            comment="Seconds since contest start when the clarification was acquired by a judge for answering.",
        ),
    )

    # --- drop contest_languages junction table ---
    op.drop_table("contest_languages")
