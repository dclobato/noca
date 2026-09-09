#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""ORM models for Arena problems, test cases, and problem ratings.

These models map onto the Core tables defined in
shared.db_schema.arena.arena_problems and add business-logic helpers:

  - ArenaProblem: problem entity with public number, statement, limits, and relationships.
  - ArenaTestCase: individual test case stored as text in the database.
  - ArenaRatingProblem: statistics and rating record for a problem (1:1).
"""

from __future__ import annotations

from datetime import datetime
from math import exp
from typing import TYPE_CHECKING

from sqlalchemy import event
from sqlalchemy.orm import Mapped, Session, relationship

from arena.database import ArenaBase
from arena.models.mixins import BadgeColorMixin
from shared.db_schema.arena import arena_collections as arena_collections_table
from shared.db_schema.arena import arena_problem_categories as arena_problem_categories_table
from shared.db_schema.arena import arena_problem_category_map as arena_problem_category_map_table
from shared.db_schema.arena import arena_problem_custom_validators as arena_problem_custom_validators_table
from shared.db_schema.arena import arena_problem_ratings as arena_problem_ratings_table
from shared.db_schema.arena import arena_problems as arena_problems_table
from shared.db_schema.arena import arena_sample_interactions as arena_sample_interactions_table
from shared.db_schema.arena import arena_test_cases as arena_test_cases_table
from shared.enumerations import (
    ArenaEditorialReleasePolicy,
    CustomValidatorActiveState,
    CustomValidatorCandidateState,
    ProblemValidatorType,
    StatementLanguage,
)
from shared.services.arena_rating import CONFIDENCE_SCALE
from shared.services.validator_type_guard import guard_validator_type_immutability

if TYPE_CHECKING:
    from arena.models.arena_submissions import ArenaSubmission, ArenaUserSolvedProblem, ArenaUserTriedProblem


class ArenaProblem(ArenaBase):
    """ORM model for an Arena problem.

    Maps onto the `arena_problems` Core table. The UUID ``id`` remains the
    relational identifier; ``arena_number`` is the public sequential reference.
    Owns a collection of ``ArenaTestCase`` records and a single
    ``ArenaRatingProblem`` record. Both are cascade-deleted when the problem is
    removed.
    """

    __table__ = arena_problems_table

    id: Mapped[str]
    arena_number: Mapped[int]
    title: Mapped[str]
    time_limit_ms: Mapped[int]
    memory_limit_kb: Mapped[int]
    pids_limit: Mapped[int]
    output_limit_in_bytes: Mapped[int]
    owner_id: Mapped[str]
    collection_id: Mapped[str | None]
    author: Mapped[str | None]
    author_is_owner: Mapped[bool]
    source: Mapped[str | None]
    hide_author_show_source: Mapped[bool]
    enabled: Mapped[bool]
    problem_statement: Mapped[str]
    editorial: Mapped[str | None]
    editorial_release_policy: Mapped[ArenaEditorialReleasePolicy]
    problem_image_base64: Mapped[str | None]
    problem_image_mime: Mapped[str | None]
    problem_image_caption: Mapped[str | None]
    notes: Mapped[str | None]
    license: Mapped[str | None]
    statement_language: Mapped[StatementLanguage | None]
    expected_difficulty: Mapped[int | None]
    validator_type: Mapped[ProblemValidatorType]
    artifact_generation: Mapped[int]
    public_export_generation: Mapped[int]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    submissions: Mapped[list[ArenaSubmission]] = relationship(
        "ArenaSubmission",
        back_populates="problem",
        foreign_keys="ArenaSubmission.problem_id",
    )
    solver_links: Mapped[list[ArenaUserSolvedProblem]] = relationship(
        "ArenaUserSolvedProblem",
        back_populates="problem",
        cascade="all, delete-orphan",
        foreign_keys="ArenaUserSolvedProblem.problem_id",
    )
    tried_links: Mapped[list[ArenaUserTriedProblem]] = relationship(
        "ArenaUserTriedProblem",
        back_populates="problem",
        cascade="all, delete-orphan",
        foreign_keys="ArenaUserTriedProblem.problem_id",
    )

    test_cases: Mapped[list[ArenaTestCase]] = relationship(
        "ArenaTestCase",
        back_populates="problem",
        cascade="all, delete-orphan",
        lazy="select",
    )

    rating: Mapped[ArenaRatingProblem | None] = relationship(
        "ArenaRatingProblem",
        back_populates="problem",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="select",
    )

    collection: Mapped[ArenaCollection | None] = relationship(
        "ArenaCollection",
        back_populates="problems",
        lazy="select",
    )
    categories: Mapped[list[ArenaCategory]] = relationship(
        "ArenaCategory",
        secondary=arena_problem_category_map_table,
        back_populates="problems",
        lazy="select",
    )
    custom_validator: Mapped[ArenaProblemCustomValidator | None] = relationship(
        "ArenaProblemCustomValidator",
        back_populates="problem",
        cascade="all, delete-orphan",
        uselist=False,
    )
    sample_interactions: Mapped[list[ArenaSampleInteraction]] = relationship(
        "ArenaSampleInteraction",
        back_populates="problem",
        order_by="ArenaSampleInteraction.ordinal",
        cascade="all, delete-orphan",
        lazy="select",
    )


class ArenaSampleInteraction(ArenaBase):
    """An author-written sample conversation shown for an interactive problem."""

    __table__ = arena_sample_interactions_table

    id: Mapped[str]
    problem_id: Mapped[str]
    ordinal: Mapped[int]
    transcript: Mapped[dict[str, object]]
    explanation: Mapped[str | None]
    hidden_at: Mapped[datetime | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    problem: Mapped[ArenaProblem] = relationship(
        "ArenaProblem",
        back_populates="sample_interactions",
    )


class ArenaTestCase(ArenaBase):
    """ORM model for a single Arena test case.

    Input and output content live on the shared filesystem under
    ``<root>/arena/<problem_id>/NNN.in|out``; the database keeps only metadata
    and the normalized (LF) on-disk byte sizes. ``ordinal`` is 1-based and unique
    within a problem.
    """

    __table__ = arena_test_cases_table

    id: Mapped[str]
    problem_id: Mapped[str]
    ordinal: Mapped[int]
    is_sample: Mapped[bool]
    input_size_bytes: Mapped[int | None]
    output_size_bytes: Mapped[int | None]
    explanation: Mapped[str | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    problem: Mapped[ArenaProblem] = relationship(
        "ArenaProblem",
        back_populates="test_cases",
    )


class ArenaProblemCustomValidator(ArenaBase):
    """Staged and active custom validator revisions for an Arena problem."""

    __table__ = arena_problem_custom_validators_table

    problem_id: Mapped[str]
    active_language_id: Mapped[str | None]
    active_source: Mapped[str | None]
    active_state: Mapped[CustomValidatorActiveState | None]
    active_validated_at: Mapped[datetime | None]
    candidate_language_id: Mapped[str | None]
    candidate_source: Mapped[str | None]
    candidate_token: Mapped[str | None]
    candidate_state: Mapped[CustomValidatorCandidateState | None]
    candidate_compile_log: Mapped[str | None]
    candidate_validated_at: Mapped[datetime | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    problem: Mapped[ArenaProblem] = relationship("ArenaProblem", back_populates="custom_validator")


class ArenaRatingProblem(ArenaBase):
    """ORM model for problem statistics and rating (1:1 with ArenaProblem).

    Tracks attempt/solve counts and provides derived metrics as computed
    properties. The ``rating_confidence`` score uses exponential decay and
    approaches 100 as ``attempted_users`` grows past ~90.
    """

    __table__ = arena_problem_ratings_table

    problem_id: Mapped[str]
    attempted_users: Mapped[int]
    solved_users: Mapped[int]
    total_submissions: Mapped[int]
    total_tries_before_solve: Mapped[int]
    rating: Mapped[int]
    dta_rating_update: Mapped[datetime | None]

    problem: Mapped[ArenaProblem] = relationship(
        "ArenaProblem",
        back_populates="rating",
    )

    @property
    def failed_users(self) -> int:
        """Number of users who attempted the problem but did not solve it.

        Returns:
            int: attempted_users minus solved_users.
        """
        return self.attempted_users - self.solved_users

    @property
    def solve_rate(self) -> float:
        """Fraction of users who solved the problem.

        Returns:
            float: solved / attempted, or 0.0 if no attempts.
        """
        if self.attempted_users == 0:
            return 0.0
        return self.solved_users / self.attempted_users

    @property
    def avg_tries_before_solve(self) -> float:
        """Average number of submissions required to reach the first accepted solution.

        Returns:
            float: total_tries_before_solve / solved_users, or 0.0 if nobody solved it.
        """
        if self.solved_users == 0:
            return 0.0
        return self.total_tries_before_solve / self.solved_users

    @property
    def rating_confidence(self) -> int:
        """Statistical confidence in the current rating (0–100).

        Uses exponential decay: approaches 100 as attempted_users grows.
        Reaches ~63 at 30 attempts, ~95 at ~90 attempts.

        Returns:
            int: Confidence score between 0 and 100.
        """
        return int(100 * (1 - exp(-self.attempted_users / CONFIDENCE_SCALE)))


class ArenaCollection(ArenaBase):
    """ORM model for a problem collection.

    A collection is an event (ICPC, Maratona SBC, InterIF) or a class
    (Iniciantes, Expressoes regulares). Unlike categories, a problem belongs to
    at most one collection, enforced by the nullable ``collection_id`` foreign
    key on ``arena_problems`` rather than a junction table. Filtering by
    collection therefore narrows (AND) the category tag set, which ORs.

    Deliberately plain: a collection is a name and a slug. It carries no badge
    color, so nothing here renders as a colored pill the way a category does.
    """

    __table__ = arena_collections_table

    id: Mapped[str]
    name: Mapped[str]
    slug: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    problems: Mapped[list[ArenaProblem]] = relationship(
        "ArenaProblem",
        back_populates="collection",
        lazy="select",
    )


class ArenaCategory(BadgeColorMixin, ArenaBase):
    """ORM model for a problem category tag.

    Categories form a flat taxonomy: each category has a unique human-readable
    ``name`` and a URL-safe ``slug``. Problems reference categories through the
    ``arena_problem_category_map`` junction table (many-to-many, no extra
    payload on the link).
    """

    __table__ = arena_problem_categories_table

    id: Mapped[str]
    name: Mapped[str]
    slug: Mapped[str]
    color: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    problems: Mapped[list[ArenaProblem]] = relationship(
        "ArenaProblem",
        secondary=arena_problem_category_map_table,
        back_populates="categories",
        lazy="select",
    )


@event.listens_for(Session, "before_flush")
def _guard_arena_problem_validator_type(
    session: Session,
    flush_context: object,
    instances: object,
) -> None:
    """Refuse a flush that changes a persisted problem's validation strategy."""
    guard_validator_type_immutability(session, (ArenaProblem,))
