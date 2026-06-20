#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest service package."""

from .authorization import (
    clear_chief_judge_if_no_longer_judge,
    ensure_contest_admin_or_uberadmin,
    ensure_contest_has_sites,
    validate_chief_judge_assignment,
)
from .creation import build_blank_contest_form, create_contest_with_owner
from .metadata import (
    ContestMetadataInput,
    ContestMetadataResult,
    ContestMetadataView,
    build_contest_clock_payload,
    build_contest_metadata_form_data,
    build_contest_metadata_view,
    build_contest_metadata_view_with_sites,
    contest_metadata_validation_errors,
    contest_status_label,
    get_active_contests_grouped,
    update_contest_metadata,
    validate_contest_language_selection,
    validate_contest_metadata_update,
)
from .models import ContestCreationResult, ContestDashboardGroups
from .queries import (
    get_active_languages,
    get_contest_by_id,
    get_contest_by_slug,
    get_contest_language_ids,
    get_inactive_contests,
)
from .updates import deactivate_past_contest
from .validation import validate_contest_metadata_fields as _validate_contest_metadata_fields

__all__ = [
    "ContestCreationResult",
    "ContestDashboardGroups",
    "ContestMetadataInput",
    "ContestMetadataResult",
    "ContestMetadataView",
    "build_blank_contest_form",
    "build_contest_clock_payload",
    "build_contest_metadata_form_data",
    "build_contest_metadata_view",
    "build_contest_metadata_view_with_sites",
    "clear_chief_judge_if_no_longer_judge",
    "contest_metadata_validation_errors",
    "contest_status_label",
    "create_contest_with_owner",
    "deactivate_past_contest",
    "ensure_contest_admin_or_uberadmin",
    "ensure_contest_has_sites",
    "get_active_contests_grouped",
    "get_active_languages",
    "get_contest_by_id",
    "get_contest_by_slug",
    "get_contest_language_ids",
    "get_inactive_contests",
    "_validate_contest_metadata_fields",
    "update_contest_metadata",
    "validate_chief_judge_assignment",
    "validate_contest_language_selection",
    "validate_contest_metadata_update",
]
