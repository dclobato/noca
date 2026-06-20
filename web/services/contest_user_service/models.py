#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared data models for contest user management."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from shared.enumerations import RoleEnum
from web.models.users import User

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_FORBIDDEN_ROLES = {RoleEnum.UBERADMIN}
_ROLE_NAME_MAP = {
    "admin": RoleEnum.ADMIN,
    "judge": RoleEnum.JUDGE,
    "staff": RoleEnum.STAFF,
    "team": RoleEnum.TEAM,
    "user": RoleEnum.USER,
}
_ROLE_EXPORT_MAP = {value: key for key, value in _ROLE_NAME_MAP.items()}
_CSV_REQUIRED_HEADERS = {"username", "fullname", "role", "password"}
_CSV_OPTIONAL_HEADERS = {"site", "location", "email"}
BatchUserRow = dict[str, object]
EMAIL_UNSET = object()


@dataclass
class UserImportResult:
    """Per-user outcome for a batch import operation."""

    username: str
    fullname: str
    role: str
    email: str | None
    status: str
    password: str | None
    site: str | None
    location: str | None
    detail: str | None


@dataclass
class BatchImportResult:
    """Aggregate result for a batch contest-user import."""

    created: int
    updated: int
    failed: int
    skipped: int
    results: list[UserImportResult] = field(default_factory=list)


@dataclass
class ContestUserGroups:
    """Contest users grouped by role for presentation in admin screens."""

    admin_users: RoleUserGroups
    judge_users: RoleUserGroups
    staff_users: RoleUserGroups
    team_users: RoleUserGroups
    user_users: RoleUserGroups


@dataclass
class SiteUserGroup:
    """Users grouped under a single contest site."""

    key: str
    label: str
    normalized_name: str
    users: list[User]

    @property
    def total_users(self) -> int:
        """Return the number of users assigned to the site group."""
        return len(self.users)


@dataclass
class RoleUserGroups:
    """Grouped representation for one role bucket on the enrolled users page."""

    ungrouped_users: list[User]
    site_groups: list[SiteUserGroup]

    @property
    def total_users(self) -> int:
        """Return the total number of users in the role bucket."""
        return len(self.ungrouped_users) + sum(group.total_users for group in self.site_groups)
