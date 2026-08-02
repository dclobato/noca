#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""GitHub Container Registry client for the image retention script.

GHCR does not delete tags: it deletes *package versions*, and one version is one
manifest digest that may carry several tags at once. A release build tags the
same digest both ``latest`` and ``vMAJOR.MINOR.PATCH``, so deleting the version
that holds an out-of-retention tag would take ``latest`` down with it.

The rule this module enforces is therefore: **a version is deletable only when
every tag it carries is deletable**. A version holding a protected tag, an
unknown tag, or a still-retained version tag is left alone, and the caller is
told why.

Untagged versions are the other half of the problem. The per-architecture
children of a multi-platform image appear as untagged versions, so deleting a
release index leaves its children behind as unreachable garbage, while the
children of a *live* release must be preserved. Neither age nor download count
separates the two cases. :func:`plan_orphan_versions` therefore decides by
reachability, using the digests that surviving manifests actually reference
(resolved by :mod:`scripts.registry_cleanup.oci`).

Authentication uses a classic personal access token with the ``read:packages``
and ``delete:packages`` scopes, or a ``GITHUB_TOKEN`` holding admin permission
on the packages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from scripts.registry_cleanup.policy import RepositoryPlan, plan_repository

_BASE_URL = "https://api.github.com"
_PAGE_SIZE = 100
_TIMEOUT = httpx.Timeout(30.0)


class GHCRError(RuntimeError):
    """Raised when the GitHub API answers a request with an unexpected status."""


@dataclass(frozen=True)
class PackageVersion:
    """One container package version (a manifest digest and its tags)."""

    version_id: int
    digest: str
    created_at: datetime
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_untagged(self) -> bool:
        """Whether the version carries no tag at all."""
        return not self.tags

    def age_days(self, now: datetime) -> float:
        """Return the version age in days.

        Args:
            now: The reference instant, timezone-aware.

        Returns:
            The age in fractional days.
        """
        return (now - self.created_at).total_seconds() / 86400.0


class GHCRClient:
    """Minimal GitHub Packages API client for container packages."""

    def __init__(self, token: str, owner: str, owner_is_org: bool) -> None:
        """Prepare an authenticated client.

        Args:
            token: GitHub token with ``read:packages`` and ``delete:packages``.
            owner: The user or organization owning the packages.
            owner_is_org: Whether ``owner`` is an organization.
        """
        self._owner = owner
        self._owner_is_org = owner_is_org
        self._client = httpx.Client(
            base_url=_BASE_URL,
            timeout=_TIMEOUT,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def __enter__(self) -> GHCRClient:
        """Enter the context manager."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    @property
    def _owner_path(self) -> str:
        """The API path prefix for the configured owner."""
        if self._owner_is_org:
            return f"/orgs/{self._owner}"
        return "/user"

    def list_packages(self) -> list[str]:
        """List every container package name owned by the configured owner.

        Returns:
            The package names, for example ``noca/webapp``.
        """
        names: list[str] = []
        for page in self._paginate(f"{self._owner_path}/packages", {"package_type": "container"}):
            names.extend(str(item["name"]) for item in page)
        return names

    def list_versions(self, package: str) -> list[PackageVersion]:
        """List every version of one container package.

        Args:
            package: The package name, for example ``noca/webapp``.

        Returns:
            The package versions with their tags.
        """
        path = f"{self._owner_path}/packages/container/{quote(package, safe='')}/versions"
        versions: list[PackageVersion] = []
        for page in self._paginate(path, {}):
            for item in page:
                container = (item.get("metadata") or {}).get("container") or {}
                versions.append(
                    PackageVersion(
                        version_id=int(item["id"]),
                        digest=str(item.get("name", "")),
                        created_at=_parse_timestamp(str(item["created_at"])),
                        tags=tuple(container.get("tags") or ()),
                    )
                )
        return versions

    def delete_version(self, package: str, version_id: int) -> None:
        """Delete one package version.

        Args:
            package: The package name owning the version.
            version_id: The numeric version identifier.

        Raises:
            GHCRError: If the deletion is refused. A version that is already
                gone (``404``) is treated as success.
        """
        path = f"{self._owner_path}/packages/container/{quote(package, safe='')}/versions/{version_id}"
        response = self._client.delete(path)
        accepted = {httpx.codes.NO_CONTENT, httpx.codes.OK, httpx.codes.NOT_FOUND}
        if response.status_code in accepted:
            return
        raise GHCRError(
            f"Deleting {package} version {version_id} failed ({response.status_code}): {response.text.strip()[:200]}"
        )

    def _paginate(self, path: str, params: dict[str, str]) -> list[list[dict[str, Any]]]:
        """Fetch every page of a paginated list endpoint.

        Args:
            path: The API path to fetch.
            params: Extra query parameters shared by every page.

        Returns:
            One list of decoded items per page.

        Raises:
            GHCRError: If any page was not answered with ``200``.
        """
        pages: list[list[dict[str, Any]]] = []
        page_number = 1
        while True:
            query = {**params, "per_page": str(_PAGE_SIZE), "page": str(page_number)}
            response = self._client.get(path, params=query)
            if response.status_code != httpx.codes.OK:
                raise GHCRError(f"GET {path} failed ({response.status_code}): {response.text.strip()[:200]}")
            items: list[dict[str, Any]] = response.json()
            if not items:
                break
            pages.append(items)
            if len(items) < _PAGE_SIZE:
                break
            page_number += 1
        return pages


@dataclass(frozen=True)
class VersionDecision:
    """The outcome of applying the retention policy to one package version."""

    version: PackageVersion
    delete: bool
    reason: str


def plan_tagged_versions(
    package: str,
    versions: list[PackageVersion],
    keep_majors: int,
) -> tuple[RepositoryPlan, list[VersionDecision]]:
    """Decide which *tagged* versions of a GHCR package may be deleted.

    A tagged version is deletable only when every tag it carries falls out of
    retention, so a digest shared with ``latest`` or with a still-supported
    release survives. Untagged versions are not considered here: they are
    resolved by :func:`plan_orphan_versions` once parentage is known.

    Args:
        package: The package name, used for reporting.
        versions: Every version of the package.
        keep_majors: How many major series to keep, newest first.

    Returns:
        The tag-level plan and one decision per tagged version.
    """
    all_tags = [tag for version in versions for tag in version.tags]
    plan = plan_repository(package, all_tags, keep_majors)
    doomed = plan.delete_names

    decisions: list[VersionDecision] = []
    for version in versions:
        if version.is_untagged:
            continue
        remaining = [tag for tag in version.tags if tag not in doomed]
        if remaining:
            kept = ", ".join(sorted(remaining))
            decisions.append(VersionDecision(version, False, f"shares digest with {kept}"))
        else:
            removed = ", ".join(sorted(version.tags))
            decisions.append(VersionDecision(version, True, f"all tags out of retention: {removed}"))

    return plan, decisions


def plan_orphan_versions(
    versions: list[PackageVersion],
    referenced: set[str],
    min_age_days: float,
    now: datetime,
) -> list[VersionDecision]:
    """Decide which untagged versions are unreachable and may be deleted.

    An untagged version is a *child* of a multi-platform index — one per
    platform, plus BuildKit attestations. It is reachable exactly while some
    surviving index still references its digest, so ``referenced`` is the union
    of the children of every version that this run keeps.

    The age guard covers the one case references cannot: during a push, children
    are uploaded before the index that will reference them exists, so a very
    recent unreferenced digest may be a build in flight rather than an orphan.

    Args:
        versions: Every version of the package.
        referenced: Digests referenced by surviving manifests.
        min_age_days: Minimum age before an unreferenced version qualifies.
        now: The reference instant, timezone-aware.

    Returns:
        One decision per untagged version.
    """
    decisions: list[VersionDecision] = []
    for version in versions:
        if not version.is_untagged:
            continue
        age = version.age_days(now)
        if version.digest in referenced:
            decisions.append(VersionDecision(version, False, "referenced by a surviving image"))
        elif age < min_age_days:
            decisions.append(VersionDecision(version, False, f"unreferenced but only {age:.1f}d old"))
        else:
            decisions.append(VersionDecision(version, True, f"orphaned child, {age:.1f}d old"))
    return decisions


def _parse_timestamp(value: str) -> datetime:
    """Parse a GitHub ISO-8601 timestamp into an aware datetime.

    Args:
        value: The timestamp as returned by the API, for example
            ``2026-07-18T10:11:12Z``.

    Returns:
        The parsed timestamp in UTC.
    """
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
