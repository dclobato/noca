#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Validation and normalization helpers for contest users."""

from .credentials import (
    ensure_role_allowed,
    normalize_optional_email,
    normalize_username,
    parse_import_role,
    parse_single_user_role,
    resolve_password,
    resolve_password_core,
    resolve_password_with_detail,
    validate_create_user_form,
    validate_edit_user_form,
)
from .imports import normalize_batch_users_payload, parse_batch_upload
from .permissions import (
    ensure_contest_user_add_or_edit_allowed,
    ensure_contest_user_remove_allowed,
    ensure_user_edit_allowed,
    ensure_user_photo_removal_allowed,
    ensure_user_photo_upload_allowed,
)
from .sites import (
    build_user_export_row,
    resolve_or_create_import_site,
    resolve_site_for_user,
    role_requires_site,
    validate_role_site_requirement,
)

__all__ = [
    "build_user_export_row",
    "ensure_contest_user_add_or_edit_allowed",
    "ensure_contest_user_remove_allowed",
    "ensure_role_allowed",
    "ensure_user_edit_allowed",
    "ensure_user_photo_removal_allowed",
    "ensure_user_photo_upload_allowed",
    "normalize_batch_users_payload",
    "normalize_optional_email",
    "normalize_username",
    "parse_batch_upload",
    "parse_import_role",
    "parse_single_user_role",
    "resolve_or_create_import_site",
    "resolve_password",
    "resolve_password_core",
    "resolve_password_with_detail",
    "resolve_site_for_user",
    "role_requires_site",
    "validate_create_user_form",
    "validate_edit_user_form",
    "validate_role_site_requirement",
]
