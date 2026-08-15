#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Resolve the validation strategy named by a creation-chooser route parameter.

A problem's strategy is chosen once, on the way into the creation editor, and is
immutable afterwards. Both the Web and Arena chooser flows name it in the URL
(``/new/{validator_type}``) rather than in an editable form field, so no crafted
POST can select or change one.

Three outcomes must stay distinguishable, which is why this returns through two
exception types rather than an ``Optional``:

* a **choosable** strategy -- ``standard`` or ``interactive`` -- opens its editor;
* the **reserved** ``checker`` value parses but has no runtime in this release,
  so the caller redirects back to the chooser with an explanatory message rather
  than pretending the URL does not exist;
* anything else is **unknown** and answers ``404``.

The route parameter is deliberately typed ``str`` at the handler and resolved
here. Typed as the enum, FastAPI answers ``422`` before the handler runs, and
``shared.error_handlers`` renders that as a neutral JSON ``{"error": ...}`` body
-- wrong for an HTML admin page, and it would make the reserved and unknown
outcomes indistinguishable.
"""

from __future__ import annotations

from shared.enumerations import ProblemValidatorType

#: Strategies a creation chooser may open an editor for, in display order.
CHOOSABLE_VALIDATOR_TYPES: tuple[ProblemValidatorType, ...] = (
    ProblemValidatorType.STANDARD,
    ProblemValidatorType.INTERACTIVE,
)

#: Operator-facing message for the reserved-but-unavailable strategy.
VALIDATOR_CHOICE_UNAVAILABLE_MESSAGE = "Output checker validation is not available in this build."


class UnknownValidatorChoiceError(ValueError):
    """Raised when a chooser route parameter names no known strategy."""


class UnavailableValidatorChoiceError(ValueError):
    """Raised when a chooser route parameter names a reserved, unbuilt strategy."""


def resolve_validator_choice(raw: str) -> ProblemValidatorType:
    """Return the strategy a creation-chooser route parameter names.

    Args:
        raw: The raw ``{validator_type}`` path segment.

    Returns:
        ProblemValidatorType: The choosable strategy the segment names.

    Raises:
        UnavailableValidatorChoiceError: If the segment names ``checker``, which
            is reserved in the stored vocabulary but has no runtime here.
        UnknownValidatorChoiceError: If the segment names no strategy at all.
    """
    try:
        strategy = ProblemValidatorType(raw)
    except ValueError as exc:
        raise UnknownValidatorChoiceError(f"Unknown validation strategy: {raw!r}.") from exc
    if strategy not in CHOOSABLE_VALIDATOR_TYPES:
        raise UnavailableValidatorChoiceError(VALIDATOR_CHOICE_UNAVAILABLE_MESSAGE)
    return strategy
