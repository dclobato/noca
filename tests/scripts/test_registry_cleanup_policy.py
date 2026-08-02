#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the container image retention policy."""

from datetime import UTC, datetime

from scripts.registry_cleanup.dockerhub import DockerHubClient, DockerHubTag
from scripts.registry_cleanup.ghcr import PackageVersion, plan_orphan_versions, plan_tagged_versions
from scripts.registry_cleanup.policy import TagKind, classify_tag, plan_repository

_NOW = datetime(2026, 8, 2, tzinfo=UTC)


def test_classify_tag_recognizes_the_published_tag_vocabulary() -> None:
    """Floating, application, and judge slot tags are each classified."""
    assert classify_tag("latest").kind is TagKind.PROTECTED
    assert classify_tag("compile").kind is TagKind.PROTECTED
    assert classify_tag("run").kind is TagKind.PROTECTED

    app = classify_tag("v15.0.1")
    assert app.kind is TagKind.VERSIONED
    assert (app.slot, app.version) == ("", (15, 0, 1))

    judge = classify_tag("compile-v14.3.2")
    assert judge.kind is TagKind.VERSIONED
    assert (judge.slot, judge.version) == ("compile", (14, 3, 2))

    assert classify_tag("nightly-2026-08-01").kind is TagKind.UNKNOWN


def test_plan_repository_keeps_the_newest_majors_whole() -> None:
    """Every patch of a retained major survives; older majors do not."""
    plan = plan_repository(
        "dclobato/noca-webapp",
        ["latest", "v15.0.1", "v15.0.0", "v14.3.2", "v14.0.0", "v13.9.9", "v12.0.0"],
        keep_majors=2,
    )

    assert plan.kept_majors == {14, 15}
    assert plan.delete_names == {"v13.9.9", "v12.0.0"}
    assert "latest" in {info.tag for info in plan.keep}


def test_plan_repository_never_deletes_unknown_tags() -> None:
    """An unrecognized tag is reported instead of being deleted."""
    plan = plan_repository("noca/webapp", ["v15.0.0", "v9.0.0", "edge"], keep_majors=1)

    assert plan.delete_names == {"v9.0.0"}
    assert [info.tag for info in plan.unknown] == ["edge"]


def test_plan_repository_counts_majors_per_slot_independently_of_naming() -> None:
    """Judge slot tags share the repository's major retention."""
    plan = plan_repository(
        "dclobato/noca-judge-python",
        ["compile", "run", "compile-v15.0.1", "run-v15.0.1", "compile-v13.0.0", "run-v13.0.0"],
        keep_majors=1,
    )

    assert plan.kept_majors == {15}
    assert plan.delete_names == {"compile-v13.0.0", "run-v13.0.0"}


def test_plan_repository_keeps_everything_when_retention_is_not_positive() -> None:
    """A misconfigured retention count deletes nothing."""
    plan = plan_repository("noca/arena", ["v15.0.0", "v1.0.0"], keep_majors=0)

    assert plan.delete_names == set()


def _version(version_id: int, *tags: str, age_days: int = 400) -> PackageVersion:
    """Build a GHCR package version for the tests.

    Args:
        version_id: The numeric version identifier.
        *tags: The tags carried by the version.
        age_days: How many days ago the version was created.

    Returns:
        The constructed :class:`PackageVersion`.
    """
    created = datetime.fromtimestamp(_NOW.timestamp() - age_days * 86400, tz=UTC)
    return PackageVersion(
        version_id=version_id,
        digest=f"sha256:{version_id:064d}",
        created_at=created,
        tags=tags,
    )


def test_ghcr_keeps_a_digest_that_still_carries_a_retained_tag() -> None:
    """A digest shared between `latest` and an old version tag is not deleted."""
    versions = [
        _version(1, "latest", "v15.0.1"),
        _version(2, "v14.0.0"),
        _version(3, "v11.0.0"),
    ]

    _, decisions = plan_tagged_versions("noca/webapp", versions, keep_majors=2)
    deleted = {decision.version.version_id for decision in decisions if decision.delete}

    assert deleted == {3}


def test_ghcr_tag_planning_ignores_untagged_versions() -> None:
    """Untagged children are decided by parentage, not by the tag policy."""
    versions = [_version(1, "latest", "v15.0.0"), _version(2)]

    _, decisions = plan_tagged_versions("noca/arena", versions, keep_majors=2)

    assert [decision.version.version_id for decision in decisions] == [1]


def test_orphan_planning_keeps_children_of_a_surviving_index() -> None:
    """A platform child referenced by a kept release is never deleted."""
    child = _version(2)
    versions = [_version(1, "latest", "v15.0.0"), child]

    decisions = plan_orphan_versions(versions, {child.digest}, min_age_days=1.0, now=_NOW)

    assert [decision.delete for decision in decisions] == [False]
    assert "referenced" in decisions[0].reason


def test_orphan_planning_deletes_children_of_a_removed_index() -> None:
    """A child left behind by a deleted release is unreachable and removed."""
    kept_child = _version(2)
    orphan = _version(3)
    versions = [_version(1, "latest", "v15.0.0"), kept_child, orphan]

    decisions = plan_orphan_versions(versions, {kept_child.digest}, min_age_days=1.0, now=_NOW)
    deleted = {decision.version.version_id for decision in decisions if decision.delete}

    assert deleted == {orphan.version_id}


def test_orphan_planning_spares_an_unreferenced_child_of_a_push_in_flight() -> None:
    """A very recent unreferenced digest may be a build mid-push, so it stays."""
    fresh = _version(2, age_days=0)
    versions = [_version(1, "latest", "v15.0.0"), fresh]

    decisions = plan_orphan_versions(versions, set(), min_age_days=1.0, now=_NOW)

    assert [decision.delete for decision in decisions] == [False]


def _tag(name: str, digest_id: int) -> DockerHubTag:
    """Build a Docker Hub tag pointing at a synthetic index digest.

    Args:
        name: The tag name.
        digest_id: Identifier used to build a distinct digest.

    Returns:
        The constructed :class:`DockerHubTag`.
    """
    return DockerHubTag(name=name, digest=f"sha256:{digest_id:064d}")


def test_dockerhub_reclaims_only_indexes_left_with_no_tag() -> None:
    """An index shared with a surviving tag is kept; a fully orphaned one is not."""
    tags = [
        _tag("latest", 1),
        _tag("v15.0.1", 1),
        _tag("v13.0.0", 2),
        _tag("v12.0.0", 3),
    ]

    stranded = DockerHubClient.unreferenced_digests(tags, {"v13.0.0", "v12.0.0"})

    assert set(stranded) == {f"sha256:{2:064d}", f"sha256:{3:064d}"}


def test_dockerhub_keeps_an_index_when_one_of_its_tags_survives() -> None:
    """Deleting `v15.0.1` must not strand the index `latest` still points at."""
    tags = [_tag("latest", 1), _tag("v15.0.1", 1)]

    assert DockerHubClient.unreferenced_digests(tags, {"v15.0.1"}) == []
