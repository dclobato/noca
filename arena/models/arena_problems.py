#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
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

from sqlalchemy.orm import Mapped, relationship

from arena.database import ArenaBase
from shared.db_schema.arena import arena_problem_categories as arena_problem_categories_table
from shared.db_schema.arena import arena_problem_category_map as arena_problem_category_map_table
from shared.db_schema.arena import arena_problem_ratings as arena_problem_ratings_table
from shared.db_schema.arena import arena_problems as arena_problems_table
from shared.db_schema.arena import arena_test_cases as arena_test_cases_table
from shared.services.arena_rating import CONFIDENCE_SCALE

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
    author: Mapped[str | None]
    author_is_owner: Mapped[bool]
    source: Mapped[str | None]
    hide_author_show_source: Mapped[bool]
    enabled: Mapped[bool]
    problem_statement: Mapped[str]
    problem_image_base64: Mapped[str | None]
    problem_image_mime: Mapped[str | None]
    problem_image_caption: Mapped[str | None]
    notes: Mapped[str | None]
    license: Mapped[str | None]
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

    categories: Mapped[list[ArenaCategory]] = relationship(
        "ArenaCategory",
        secondary=arena_problem_category_map_table,
        back_populates="problems",
        lazy="select",
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
    def display_rating(self) -> float:
        """Problem difficulty on the 0.1–10.0 user-facing scale.

        Divides the internal [1, 100] integer rating by 10 to produce the
        display value. Use this property wherever the rating is shown to users
        or returned in API responses.

        Returns:
            float: Display rating between 0.1 and 10.0 (or 0.0 for unrated).
        """
        return self.rating / 10.0

    @property
    def rating_confidence(self) -> int:
        """Statistical confidence in the current rating (0–100).

        Uses exponential decay: approaches 100 as attempted_users grows.
        Reaches ~63 at 30 attempts, ~95 at ~90 attempts.

        Returns:
            int: Confidence score between 0 and 100.
        """
        return int(100 * (1 - exp(-self.attempted_users / CONFIDENCE_SCALE)))


class ArenaCategory(ArenaBase):
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

    @property
    def foreground_color(self) -> str:
        """Return black or white, whichever has better WCAG contrast with ``color``.

        Returns:
            str: ``"#000000"`` for black text or ``"#ffffff"`` for white text.
        """
        hex_color = self.color.removeprefix("#")
        red = int(hex_color[0:2], 16) / 255
        green = int(hex_color[2:4], 16) / 255
        blue = int(hex_color[4:6], 16) / 255

        def linear(channel: float) -> float:
            """Convert an sRGB channel to linear light."""
            if channel <= 0.03928:
                return channel / 12.92
            return float(((channel + 0.055) / 1.055) ** 2.4)

        luminance = 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)
        contrast_with_black = (luminance + 0.05) / 0.05
        contrast_with_white = 1.05 / (luminance + 0.05)
        return "#000000" if contrast_with_black >= contrast_with_white else "#ffffff"
