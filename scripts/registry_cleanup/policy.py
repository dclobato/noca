#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Retention policy for published NOCA container tags.

This module is pure: it classifies tag names and decides what may be deleted,
without touching any registry. Both registry clients share it so Docker Hub and
GHCR can never drift apart on what "old" means.

The tag vocabulary comes from ``containers/docker-bake.hcl``:

- application images carry a floating ``latest`` plus one ``vMAJOR.MINOR.PATCH``
  tag per release;
- judge language images carry the floating slot tags ``compile`` / ``run`` plus
  ``compile-vMAJOR.MINOR.PATCH`` / ``run-vMAJOR.MINOR.PATCH``.

The retention rule is *keep the newest N major series of each repository, whole*.
Majors are counted **per repository**, not globally, so a language image whose
last build was ``v12`` keeps its own two newest majors instead of being wiped by
the application images having moved on to ``v15``.

Anything that does not match a known pattern is classified ``UNKNOWN`` and is
never proposed for deletion: an unrecognized tag is reported so a human can
decide, because guessing wrong here destroys an image.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field

_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_SLOT_VERSION_RE = re.compile(r"^(compile|run)-v(\d+)\.(\d+)\.(\d+)$")

#: Floating tags that always point at the current build and are never deleted.
PROTECTED_TAGS = frozenset({"latest", "compile", "run"})


class TagKind(enum.Enum):
    """How a tag name is treated by the retention policy."""

    PROTECTED = "protected"
    VERSIONED = "versioned"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TagInfo:
    """A classified tag name.

    Attributes:
        tag: The tag exactly as published.
        kind: Which policy branch applies to it.
        slot: ``""`` for application images, ``"compile"``/``"run"`` for judge
            images, ``None`` when the tag is not versioned.
        version: The parsed ``(major, minor, patch)`` triple, or ``None``.
    """

    tag: str
    kind: TagKind
    slot: str | None = None
    version: tuple[int, int, int] | None = None

    @property
    def major(self) -> int | None:
        """The major component of a versioned tag, if any."""
        return None if self.version is None else self.version[0]


@dataclass
class RepositoryPlan:
    """The decision taken for every tag of one repository.

    Attributes:
        repository: Human-readable repository reference, used only for output.
        kept_majors: The major series retained by the policy.
        delete: Tags whose major series fell out of retention.
        keep: Tags retained, whether protected or in a kept major.
        unknown: Tags that matched no known pattern and were left untouched.
    """

    repository: str
    kept_majors: set[int] = field(default_factory=set)
    delete: list[TagInfo] = field(default_factory=list)
    keep: list[TagInfo] = field(default_factory=list)
    unknown: list[TagInfo] = field(default_factory=list)

    @property
    def delete_names(self) -> set[str]:
        """The tag names selected for deletion."""
        return {info.tag for info in self.delete}


def classify_tag(tag: str) -> TagInfo:
    """Classify one published tag name.

    Args:
        tag: The tag name as published to the registry.

    Returns:
        The corresponding :class:`TagInfo`.
    """
    if tag in PROTECTED_TAGS:
        return TagInfo(tag=tag, kind=TagKind.PROTECTED)

    match = _VERSION_RE.match(tag)
    if match is not None:
        version = (int(match[1]), int(match[2]), int(match[3]))
        return TagInfo(tag=tag, kind=TagKind.VERSIONED, slot="", version=version)

    match = _SLOT_VERSION_RE.match(tag)
    if match is not None:
        version = (int(match[2]), int(match[3]), int(match[4]))
        return TagInfo(tag=tag, kind=TagKind.VERSIONED, slot=match[1], version=version)

    return TagInfo(tag=tag, kind=TagKind.UNKNOWN)


def select_kept_majors(tags: list[TagInfo], keep_majors: int) -> set[int]:
    """Pick the major series a repository retains.

    Args:
        tags: Every classified tag of the repository.
        keep_majors: How many major series to keep, newest first.

    Returns:
        The set of retained major numbers. Every major is retained when
        ``keep_majors`` is zero or negative, so a misconfigured run deletes
        nothing rather than everything.
    """
    majors = {info.major for info in tags if info.kind is TagKind.VERSIONED}
    present = {major for major in majors if major is not None}
    if keep_majors <= 0:
        return present
    return set(sorted(present, reverse=True)[:keep_majors])


def plan_repository(repository: str, tags: list[str], keep_majors: int) -> RepositoryPlan:
    """Decide what to delete in one repository.

    Args:
        repository: Repository reference used for reporting.
        tags: Every tag name currently published in that repository.
        keep_majors: How many major series to keep, newest first.

    Returns:
        The :class:`RepositoryPlan` describing the decision for every tag.
    """
    classified = [classify_tag(tag) for tag in tags]
    kept_majors = select_kept_majors(classified, keep_majors)
    plan = RepositoryPlan(repository=repository, kept_majors=kept_majors)

    for info in classified:
        if info.kind is TagKind.UNKNOWN:
            plan.unknown.append(info)
        elif info.kind is TagKind.VERSIONED and info.major not in kept_majors:
            plan.delete.append(info)
        else:
            plan.keep.append(info)

    plan.delete.sort(key=lambda info: (info.slot or "", info.version or (0, 0, 0)))
    return plan
