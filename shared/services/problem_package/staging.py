#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Disk-backed staging for problem-package payloads and their promotion.

An import never holds a package in RAM. Members are streamed into a staging
area, prepared in hidden sibling locations *under their configured final roots*
so every promotion is a same-filesystem rename, and only then promoted. The
promotion is reversible: if the owning transaction fails to commit, every
promoted artifact is removed again.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType

STAGING_PREFIX = "noca-pkg-"
"""Prefix shared by every temporary path this module creates, so an operator can
recognize and sweep leftovers by name."""


class PackageStagingError(RuntimeError):
    """Raised when a staging or promotion path is unsafe or unusable."""


@dataclass(slots=True)
class PackageStagingArea:
    """A temporary directory owning every extracted package payload.

    The area is created eagerly and removed by :meth:`close`, which the reader's
    context manager always calls — including on the error paths, so a refused
    package leaves nothing behind.
    """

    root: Path
    _closed: bool = field(default=False, init=False)

    @classmethod
    def create(cls, *, parent: Path | None = None) -> PackageStagingArea:
        """Create a new staging area, optionally inside a specific parent."""
        if parent is not None:
            parent.mkdir(parents=True, exist_ok=True)
        return cls(root=Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=parent)))

    def path_for(self, relative: str) -> Path:
        """Return a staged path for ``relative``, creating parent directories.

        Args:
            relative: A package-relative member name, already validated as safe.

        Returns:
            The absolute staged path.

        Raises:
            PackageStagingError: If the resolved path escapes the staging root.
        """
        candidate = (self.root / relative).resolve(strict=False)
        if not candidate.is_relative_to(self.root.resolve()):
            raise PackageStagingError(f"Staged path escapes the staging area: {relative!r}")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate

    def close(self) -> None:
        """Remove the staging area and everything in it."""
        if self._closed:
            return
        shutil.rmtree(self.root, ignore_errors=True)
        self._closed = True

    def __enter__(self) -> PackageStagingArea:
        """Enter the staging context."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Leave the staging context, removing every temporary path."""
        self.close()


@dataclass(frozen=True, slots=True)
class PlannedArtifact:
    """One artifact staged next to its final location, ready to be promoted.

    Attributes:
        staged: The hidden sibling path holding the prepared content.
        target: Where the artifact belongs once the import is accepted.
        root: The configured root ``target`` must stay inside. Recorded so the
            journal's own re-validation has something authoritative to check
            against.
    """

    staged: Path
    target: Path
    root: Path


@dataclass(slots=True)
class ArtifactPromotion:
    """Reversible promotion of staged artifacts into their final locations.

    Modeled on :class:`web.services.contest_removal_files.ContestFileQuarantine`,
    but generalized: promotion spans two different roots (statements and test
    cases) and mixes files with directories, so each move is recorded explicitly
    rather than inferred from a containing directory.
    """

    planned: list[PlannedArtifact] = field(default_factory=list)
    promoted: list[PlannedArtifact] = field(default_factory=list)

    def plan(self, staged: Path, target: Path, root: Path) -> PlannedArtifact:
        """Record an artifact to promote later, validating its target.

        Raises:
            PackageStagingError: If ``target`` is not inside ``root``.
        """
        artifact = PlannedArtifact(staged=staged, target=target, root=root)
        require_inside(target, root)
        self.planned.append(artifact)
        return artifact

    def promote(self) -> None:
        """Move every planned artifact into place with a same-filesystem rename."""
        for artifact in self.planned:
            require_inside(artifact.target, artifact.root)
            artifact.target.parent.mkdir(parents=True, exist_ok=True)
            if artifact.target.exists():
                # A directory target (test cases) may pre-exist from a partial
                # earlier attempt; replacing a non-empty directory is not a
                # rename, so clear it first.
                remove_path(artifact.target)
            artifact.staged.replace(artifact.target)
            self.promoted.append(artifact)

    def rollback(self) -> None:
        """Delete every promoted artifact after the owning commit failed."""
        for artifact in reversed(self.promoted):
            try:
                require_inside(artifact.target, artifact.root)
            except PackageStagingError:
                continue
            remove_path(artifact.target)
        self.promoted.clear()
        self.planned.clear()

    def finish(self) -> None:
        """Forget the promotion after a successful commit."""
        self.promoted.clear()
        self.planned.clear()


def hidden_sibling(target: Path, token: str) -> Path:
    """Return a hidden staging path next to ``target``, on the same filesystem."""
    return target.parent / f".{STAGING_PREFIX}{token}-{target.name}"


def new_token() -> str:
    """Return a fresh token identifying one import attempt."""
    return uuid.uuid4().hex


def require_inside(path: Path, root: Path) -> None:
    """Raise unless ``path`` resolves inside ``root``."""
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(root.resolve()):
        raise PackageStagingError(f"Artifact path escapes its configured root: {path}")


def remove_path(path: Path) -> None:
    """Delete a file or directory, tolerating its absence."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
