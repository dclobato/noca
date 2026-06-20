#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest metadata compatibility exports."""

from .forms import ContestMetadataInput
from .models import ContestMetadataResult, ContestMetadataView
from .presentation import (
    build_contest_clock_payload,
    build_contest_metadata_form_data,
    build_contest_metadata_view,
    build_contest_metadata_view_with_sites,
    contest_metadata_validation_errors,
    contest_status_label,
    get_active_contests_grouped,
)
from .updates import sync_contest_languages, update_contest_metadata, validate_contest_language_selection
from .validation import (
    validate_contest_metadata_fields,
    validate_contest_metadata_update,
    validate_contest_timing_constraints,
    validate_updated_contest_end_time,
)

__all__ = [
    "ContestMetadataInput",
    "ContestMetadataResult",
    "ContestMetadataView",
    "build_contest_clock_payload",
    "build_contest_metadata_form_data",
    "build_contest_metadata_view",
    "build_contest_metadata_view_with_sites",
    "contest_metadata_validation_errors",
    "contest_status_label",
    "get_active_contests_grouped",
    "sync_contest_languages",
    "update_contest_metadata",
    "validate_contest_language_selection",
    "validate_contest_metadata_fields",
    "validate_contest_metadata_update",
    "validate_contest_timing_constraints",
    "validate_updated_contest_end_time",
]
