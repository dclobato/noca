#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Command line entry point for the container image retention cleanup."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import UTC, datetime

from scripts.registry_cleanup.dockerhub import (
    DockerHubClient,
    DockerHubError,
    DockerHubRepository,
    DockerHubTag,
)
from scripts.registry_cleanup.dockerhub_registry import (
    DeleteOutcome,
    DockerHubRegistryClient,
    DockerRegistryError,
)
from scripts.registry_cleanup.ghcr import (
    GHCRClient,
    GHCRError,
    PackageVersion,
    VersionDecision,
    plan_orphan_versions,
    plan_tagged_versions,
)
from scripts.registry_cleanup.oci import GHCRRegistryClient, RegistryError
from scripts.registry_cleanup.policy import RepositoryPlan, plan_repository

_DEFAULT_NAMESPACE = "dclobato"
_DEFAULT_PREFIX = "noca"
_JUDGE_MARKER = "judge-"


@dataclass
class Totals:
    """Running counters used for the final summary."""

    repositories: int = 0
    deletable: int = 0
    deleted: int = 0
    failed: int = 0
    unknown: int = 0
    manifests: int = 0
    reclaimed: int = 0


def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="cleanup_registry_images",
        description=(
            "Delete out-of-retention NOCA image tags from Docker Hub and GHCR. "
            "Runs as a dry run unless --execute is given."
        ),
    )
    parser.add_argument(
        "--keep-majors",
        type=int,
        default=2,
        metavar="N",
        help="Keep the newest N major series of each repository, whole (default: 2).",
    )
    parser.add_argument(
        "--registry",
        choices=("both", "dockerhub", "ghcr"),
        default="both",
        help="Which registry to clean (default: both).",
    )
    parser.add_argument(
        "--apps-only",
        action="store_true",
        help="Skip the judge-<language> images and clean only the application images.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete. Without it nothing is removed and the plan is printed.",
    )
    parser.add_argument(
        "--dockerhub-namespace",
        default=_DEFAULT_NAMESPACE,
        help=f"Docker Hub namespace to clean (default: {_DEFAULT_NAMESPACE}).",
    )
    parser.add_argument(
        "--ghcr-owner",
        default=_DEFAULT_NAMESPACE,
        help=f"GHCR package owner (default: {_DEFAULT_NAMESPACE}).",
    )
    parser.add_argument(
        "--ghcr-owner-is-org",
        action="store_true",
        help="Treat the GHCR owner as an organization instead of a user account.",
    )
    parser.add_argument(
        "--prefix",
        default=_DEFAULT_PREFIX,
        help=(
            "Image family prefix: Docker Hub repositories named <prefix>-* and GHCR "
            f"packages named <prefix>/* (default: {_DEFAULT_PREFIX})."
        ),
    )
    parser.add_argument(
        "--keep-manifests",
        action="store_true",
        help=(
            "Docker Hub only: delete tags but leave their image indexes behind. "
            "Deleting a tag does not reclaim storage on Docker Hub, so by "
            "default any index left with no tags is deleted too."
        ),
    )
    parser.add_argument(
        "--keep-orphans",
        action="store_true",
        help=(
            "GHCR only: keep untagged versions even when no surviving image "
            "references them. By default those orphaned platform children are "
            "deleted along with the index that used to reference them."
        ),
    )
    parser.add_argument(
        "--orphan-min-age-days",
        type=float,
        default=1.0,
        metavar="DAYS",
        help=(
            "GHCR only: ignore unreferenced versions younger than this, so a "
            "push in flight is never mistaken for an orphan (default: 1)."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the cleanup.

    Args:
        argv: Command line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        ``0`` on success, ``1`` when at least one deletion failed, ``2`` on a
        configuration error.
    """
    args = build_parser().parse_args(argv)
    totals = Totals()

    mode = "EXECUTE" if args.execute else "DRY RUN"
    print(f"NOCA image retention cleanup [{mode}] — keeping the newest {args.keep_majors} major series per repository")
    print()

    try:
        if args.registry in ("both", "dockerhub"):
            _run_dockerhub(args, totals)
        if args.registry in ("both", "ghcr"):
            _run_ghcr(args, totals)
    except (DockerHubError, DockerRegistryError, GHCRError) as exc:
        print(f"error: {exc}")
        return 2

    print()
    print(
        f"Summary: {totals.repositories} repositories, {totals.deletable} tags/versions "
        f"out of retention, {totals.deleted} deleted, {totals.failed} failed, "
        f"{totals.unknown} unrecognized tags left untouched."
    )
    if totals.manifests:
        print(
            f"         Docker Hub image indexes left unreferenced: {totals.manifests}, reclaimed: {totals.reclaimed}."
        )
    if not args.execute and totals.deletable:
        print("Nothing was deleted. Re-run with --execute to apply this plan.")
    return 1 if totals.failed else 0


def _selects(name: str, prefix: str, separator: str, apps_only: bool) -> bool:
    """Decide whether a repository or package name is in scope.

    Args:
        name: The repository or package name.
        prefix: The configured image family prefix.
        separator: ``"-"`` for Docker Hub's flat naming, ``"/"`` for GHCR paths.
        apps_only: Whether judge language images are excluded.

    Returns:
        Whether the name should be cleaned.
    """
    if not name.startswith(f"{prefix}{separator}"):
        return False
    return not (apps_only and _JUDGE_MARKER in name)


def _report(plan: RepositoryPlan, totals: Totals) -> None:
    """Print the tag-level plan of one repository.

    Args:
        plan: The repository plan to report.
        totals: Counters updated with this repository's unknown tags.
    """
    kept = ", ".join(f"v{major}" for major in sorted(plan.kept_majors, reverse=True)) or "none"
    print(f"{plan.repository} — keeping majors: {kept}")
    for info in plan.unknown:
        totals.unknown += 1
        print(f"    ? {info.tag} (unrecognized, left untouched)")


def _run_dockerhub(args: argparse.Namespace, totals: Totals) -> None:
    """Clean the Docker Hub side.

    Args:
        args: Parsed command line arguments.
        totals: Counters updated in place.

    Raises:
        SystemExit: If the Docker Hub credentials are missing.
    """
    username = os.environ.get("DOCKERHUB_USERNAME")
    token = os.environ.get("DOCKERHUB_TOKEN")
    if not username or not token:
        raise SystemExit("DOCKERHUB_USERNAME and DOCKERHUB_TOKEN must be set")

    print("== Docker Hub ==")
    with (
        DockerHubClient(username, token) as client,
        DockerHubRegistryClient(username, token) as registry,
    ):
        repositories = [
            repository
            for repository in client.list_repositories(args.dockerhub_namespace)
            if _selects(repository.name, args.prefix, "-", args.apps_only)
        ]
        for repository in sorted(repositories, key=lambda item: item.name):
            _clean_dockerhub_repository(client, registry, repository, args, totals)


def _clean_dockerhub_repository(
    client: DockerHubClient,
    registry: DockerHubRegistryClient,
    repository: DockerHubRepository,
    args: argparse.Namespace,
    totals: Totals,
) -> None:
    """Apply the policy to one Docker Hub repository.

    Deleting a tag on Docker Hub leaves its image index behind as untagged
    storage, so each deleted tag is followed by a manifest deletion for any
    digest no surviving tag references.

    Args:
        client: The authenticated Docker Hub client.
        registry: The registry client used to delete manifests by digest.
        repository: The repository to clean.
        args: Parsed command line arguments.
        totals: Counters updated in place.
    """
    totals.repositories += 1
    tags = client.list_tags(repository)
    plan = plan_repository(repository.reference, [tag.name for tag in tags], args.keep_majors)
    _report(plan, totals)

    removed: set[str] = set()
    for info in plan.delete:
        totals.deletable += 1
        if not args.execute:
            print(f"    - {info.tag} (would delete)")
            removed.add(info.tag)
            continue
        try:
            client.delete_tag(repository, info.tag)
        except DockerHubError as exc:
            totals.failed += 1
            print(f"    ! {info.tag}: {exc}")
        else:
            totals.deleted += 1
            removed.add(info.tag)
            print(f"    - {info.tag} (deleted)")

    if not args.keep_manifests:
        _reclaim_dockerhub_manifests(registry, repository, tags, removed, args, totals)


def _reclaim_dockerhub_manifests(
    registry: DockerHubRegistryClient,
    repository: DockerHubRepository,
    tags: list[DockerHubTag],
    removed: set[str],
    args: argparse.Namespace,
    totals: Totals,
) -> None:
    """Delete the image indexes that the tag deletions left unreferenced.

    Args:
        registry: The registry client used to delete manifests by digest.
        repository: The repository being cleaned.
        tags: Every tag the repository had before the deletions.
        removed: The tag names that were deleted (or would be, in a dry run).
        args: Parsed command line arguments.
        totals: Counters updated in place.
    """
    for digest in DockerHubClient.unreferenced_digests(tags, removed):
        totals.manifests += 1
        if not args.execute:
            print(f"      ~ {digest[:19]} (would reclaim index)")
            continue
        try:
            outcome = registry.delete_manifest(repository.reference, digest)
        except DockerRegistryError as exc:
            totals.failed += 1
            print(f"      ! {digest[:19]}: {exc}")
        else:
            if outcome is DeleteOutcome.STILL_REFERENCED:
                print(f"      ~ {digest[:19]} kept ({outcome.value})")
            else:
                totals.reclaimed += 1
                print(f"      ~ {digest[:19]} index {outcome.value}")


def _run_ghcr(args: argparse.Namespace, totals: Totals) -> None:
    """Clean the GHCR side.

    Args:
        args: Parsed command line arguments.
        totals: Counters updated in place.

    Raises:
        SystemExit: If no GitHub token is available.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN (or GH_TOKEN) must be set")

    print()
    print("== GitHub Container Registry ==")
    now = datetime.now(UTC)
    with (
        GHCRClient(token, args.ghcr_owner, args.ghcr_owner_is_org) as client,
        GHCRRegistryClient(args.ghcr_owner, token) as registry,
    ):
        packages = [
            package for package in client.list_packages() if _selects(package, args.prefix, "/", args.apps_only)
        ]
        for package in sorted(packages):
            totals.repositories += 1
            versions = client.list_versions(package)
            plan, decisions = plan_tagged_versions(package, versions, args.keep_majors)
            _report(plan, totals)

            if not args.keep_orphans:
                survivors = [decision.version for decision in decisions if not decision.delete]
                referenced = _referenced_digests(registry, package, survivors)
                if referenced is None:
                    print("    ! parentage unknown, skipping orphan cleanup for this package")
                else:
                    decisions.extend(plan_orphan_versions(versions, referenced, args.orphan_min_age_days, now))

            _apply_ghcr_decisions(client, package, decisions, args, totals)


def _referenced_digests(
    registry: GHCRRegistryClient,
    package: str,
    survivors: list[PackageVersion],
) -> set[str] | None:
    """Collect every digest the surviving versions of a package reference.

    Args:
        registry: The registry client used to read manifests.
        package: The package being cleaned.
        survivors: The versions this run keeps.

    Returns:
        The referenced digests, or ``None`` when any manifest could not be read.
        ``None`` means "parentage unknown" and must suppress orphan deletion:
        an unreadable index is indistinguishable from one with no children, and
        guessing wrong deletes a live image's platform.
    """
    referenced: set[str] = set()
    for version in survivors:
        try:
            referenced |= registry.child_digests(package, version.digest)
        except RegistryError as exc:
            print(f"    ! {exc}")
            return None
    return referenced


def _apply_ghcr_decisions(
    client: GHCRClient,
    package: str,
    decisions: list[VersionDecision],
    args: argparse.Namespace,
    totals: Totals,
) -> None:
    """Delete (or report) the GHCR versions selected by the policy.

    Args:
        client: The authenticated GHCR client.
        package: The package being cleaned.
        decisions: One decision per package version.
        args: Parsed command line arguments.
        totals: Counters updated in place.
    """
    for decision in decisions:
        if not decision.delete:
            continue
        version = decision.version
        label = ", ".join(version.tags) if version.tags else version.digest[:19]
        totals.deletable += 1
        if not args.execute:
            print(f"    - {label} (would delete: {decision.reason})")
            continue
        try:
            client.delete_version(package, version.version_id)
        except GHCRError as exc:
            totals.failed += 1
            print(f"    ! {label}: {exc}")
        else:
            totals.deleted += 1
            print(f"    - {label} (deleted: {decision.reason})")
