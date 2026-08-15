#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""A problem's stored validation strategy is final once set.

A problem's ``validator_type`` is chosen once, at creation or import, and never
changes afterwards: an interactive problem cannot become a standard one, because
its test cases carry no expected output and its contestants were shown sample
interactions rather than sample cases.

*Choosing* it is not this module's business. A created problem takes its strategy
from the creation chooser's route parameter (``shared.services.validator_choice``)
and an imported one from its package metadata.

The service layer refuses a disagreeing value on its update paths, and this
module adds the last-resort ORM check, so a direct attribute assignment followed
by a flush fails too. Both layers are needed: a service guard cannot see an
assignment that bypasses it.

**Stated boundary:** this covers every supported application workflow. It does
not cover direct SQL, and nothing in this release detects a strategy changed with
``psql``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session


class ValidatorTypeImmutableError(ValueError):
    """Raised when a caller tries to change a problem's stored strategy."""


def raise_if_validator_type_changed(instance: Any) -> None:
    """Reject a persisted ``validator_type`` whose value was reassigned.

    A brand-new instance is exempt: setting the strategy at creation is the one
    write this rule permits. Reassigning the identical value is also permitted,
    since it changes nothing -- SQLAlchemy records no history for it.

    Args:
        instance: A mapped problem instance carrying a ``validator_type``.

    Raises:
        ValidatorTypeImmutableError: If the instance is persistent and its
            ``validator_type`` holds a changed value.
    """
    state = inspect(instance)
    if state.transient or state.pending:
        return
    history = state.attrs.validator_type.history
    if not history.deleted:
        return
    previous = history.deleted[0]
    current = history.added[0] if history.added else None
    if previous == current:
        return
    raise ValidatorTypeImmutableError(
        f"A problem's validation strategy is immutable: {previous} cannot become {current}. "
        "Create a new problem with the intended strategy instead."
    )


def guard_validator_type_immutability(session: Session, problem_types: tuple[type[Any], ...]) -> None:
    """Apply :func:`raise_if_validator_type_changed` to every dirty problem.

    Args:
        session: The session being flushed.
        problem_types: Mapped classes to inspect, e.g. ``(Problem,)``.

    Raises:
        ValidatorTypeImmutableError: If any dirty instance changed its strategy.
    """
    for obj in session.dirty:
        if isinstance(obj, problem_types):
            raise_if_validator_type_changed(obj)
