#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest user service package."""

from .batch import batch_import_users
from .crud import create_user, remove_user, update_user, update_user_credentials
from .models import BatchImportResult, ContestUserGroups, RoleUserGroups, SiteUserGroup, UserImportResult
from .queries import (
    count_contest_teams,
    count_teams_by_site,
    get_contest_user_groups,
    get_user_by_username_in_contest,
    get_user_in_contest,
    group_users_by_site,
    list_contest_sites_for_form,
    list_users_for_export,
)
from .session_binding import (
    count_bound_teams,
    count_restricted_teams,
    set_contest_team_session_policy,
)
from .validation import (
    build_user_export_row,
    ensure_contest_user_add_or_edit_allowed,
    ensure_contest_user_remove_allowed,
    ensure_user_edit_allowed,
    ensure_user_media_removal_allowed,
    ensure_user_media_upload_allowed,
    normalize_batch_users_payload,
    normalize_username,
    parse_batch_upload,
    parse_single_user_role,
    resolve_or_create_import_site,
    resolve_site_for_user,
    role_requires_site,
    validate_create_user_form,
    validate_edit_credentials_form,
    validate_edit_user_form,
    validate_role_site_requirement,
)

__all__ = [
    "BatchImportResult",
    "ContestUserGroups",
    "RoleUserGroups",
    "SiteUserGroup",
    "UserImportResult",
    "batch_import_users",
    "build_user_export_row",
    "count_bound_teams",
    "count_contest_teams",
    "count_restricted_teams",
    "count_teams_by_site",
    "create_user",
    "ensure_contest_user_add_or_edit_allowed",
    "ensure_contest_user_remove_allowed",
    "ensure_user_edit_allowed",
    "ensure_user_media_removal_allowed",
    "ensure_user_media_upload_allowed",
    "get_contest_user_groups",
    "get_user_by_username_in_contest",
    "get_user_in_contest",
    "group_users_by_site",
    "list_contest_sites_for_form",
    "list_users_for_export",
    "normalize_batch_users_payload",
    "normalize_username",
    "parse_batch_upload",
    "parse_single_user_role",
    "remove_user",
    "resolve_or_create_import_site",
    "resolve_site_for_user",
    "set_contest_team_session_policy",
    "role_requires_site",
    "update_user",
    "update_user_credentials",
    "validate_create_user_form",
    "validate_edit_credentials_form",
    "validate_edit_user_form",
    "validate_role_site_requirement",
]
