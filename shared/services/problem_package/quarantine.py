#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Displace-and-restore promotion, for artifacts that already have content.

:class:`shared.services.problem_package.staging.ArtifactPromotion` deletes
whatever it finds at its target, which is correct for an import because nothing
was there before. An edit must instead be able to put the previous content back,
so promotion here renames it to a hidden same-filesystem sibling and rollback
renames it home again — the shape of
:class:`web.services.contest_removal_files.ContestFileQuarantine`.

Two ordering rules carry the safety of this module, and both exist because a
process can die between any two lines:

* the quarantine is **recorded before the rename that replaces it**, so an
  interrupted promotion is still undoable;
* recovery believes **the disk, not a recorded snapshot**, about whether a
  displacement happened, because the snapshot can be stale by the time it is
  read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from shared.services.durable_fs import fsync_parents
from shared.services.problem_package.staging import (
    PackageStagingError,
    PlannedArtifact,
    hidden_sibling,
    remove_path,
    require_inside,
)


@dataclass(frozen=True, slots=True)
class PlannedRemoval:
    """One artifact the promotion deletes rather than replaces.

    A removal has no staged source, which is why it is its own type rather than a
    :class:`~shared.services.problem_package.staging.PlannedArtifact` with a
    nullable ``staged``: the import path shares that dataclass and must keep its
    guarantee that every planned artifact has content waiting for it.

    It exists because an edit can legitimately end with *less* on disk than it
    started with -- a Contest statement switched from PDF to Markdown drops the
    PDF -- and that deletion has to be as reversible as a replacement, or a failed
    commit would leave the problem with neither file.

    Attributes:
        target: The path to remove once the Save is accepted.
        root: The configured root ``target`` must stay inside.
    """

    target: Path
    root: Path


@dataclass(frozen=True, slots=True)
class QuarantinedArtifact:
    """One promoted artifact and the content it displaced.

    Attributes:
        planned: The artifact promoted into ``planned.target``, or the removal
            that emptied it. Both carry the ``target`` and ``root`` rollback and
            ``finish`` need, and nothing here reads a staged path.
        quarantine: Where the previous content of ``planned.target`` was moved,
            or ``None`` when the target did not exist before the promotion.
    """

    planned: PlannedArtifact | PlannedRemoval
    quarantine: Path | None


@dataclass(slots=True)
class QuarantiningPromotion:
    """Reversible promotion that displaces the previous content instead of deleting it.

    Deliberately *not* a subclass of ``ArtifactPromotion``: the import path must
    keep its exact behavior, and nothing there may change by inheritance.
    """

    token: str
    planned: list[PlannedArtifact] = field(default_factory=list)
    planned_removals: list[PlannedRemoval] = field(default_factory=list)
    promoted: list[QuarantinedArtifact] = field(default_factory=list)

    def plan(self, staged: Path, target: Path, root: Path) -> PlannedArtifact:
        """Record an artifact to promote later, validating its target.

        Raises:
            PackageStagingError: If ``target`` is not inside ``root``.
        """
        artifact = PlannedArtifact(staged=staged, target=target, root=root)
        require_inside(target, root)
        self.planned.append(artifact)
        return artifact

    def plan_removal(self, target: Path, root: Path) -> PlannedRemoval:
        """Record a path this promotion deletes, validating it.

        Removals are promoted after the replacements, so a Save that writes one
        statement format and drops the other never has both missing at once.

        Raises:
            PackageStagingError: If ``target`` is not inside ``root``.
        """
        removal = PlannedRemoval(target=target, root=root)
        require_inside(target, root)
        self.planned_removals.append(removal)
        return removal

    def quarantine_path(self, target: Path) -> Path:
        """Return where this promotion parks ``target``'s previous content."""
        return hidden_sibling(target, f"{self.token}-prev")

    def promote(self) -> None:
        """Move each staged artifact into place, quarantining what it displaces.

        The displacement is recorded **before** the rename that would strand it.
        Recording it afterwards leaves a window — the target renamed away, the
        replacement rename failing — in which rollback knows nothing about the
        quarantine and the author's content is orphaned in a hidden path no
        caller names. That window is the whole failure this module exists to
        remove, so it must not be reintroduced by moving the append down.
        """
        for artifact in self.planned:
            require_inside(artifact.target, artifact.root)
            artifact.target.parent.mkdir(parents=True, exist_ok=True)
            quarantine: Path | None = None
            if artifact.target.exists():
                quarantine = self.quarantine_path(artifact.target)
                require_inside(quarantine, artifact.root)
                remove_path(quarantine)
                artifact.target.replace(quarantine)
            self.promoted.append(QuarantinedArtifact(planned=artifact, quarantine=quarantine))
            artifact.staged.replace(artifact.target)
        for removal in self.planned_removals:
            require_inside(removal.target, removal.root)
            if not removal.target.exists():
                continue
            quarantined = self.quarantine_path(removal.target)
            require_inside(quarantined, removal.root)
            remove_path(quarantined)
            # Recorded before the rename, for the same reason a replacement is: a
            # crash in between would otherwise strand the content in a hidden path
            # no caller names.
            self.promoted.append(QuarantinedArtifact(planned=removal, quarantine=quarantined))
            removal.target.replace(quarantined)
        self._flush_renames()

    def _flush_renames(self) -> None:
        """Flush the directory entries this promotion just rewrote.

        A rename is atomic but not automatically durable: after a host crash the
        entry can be missing even though the database transaction that depended
        on it committed. Recovery cannot tell that state from a promotion that
        never happened, so the entries are flushed while the transaction is still
        open. One flush per directory, not per artifact -- promoting a problem's
        whole test-case directory touches a single parent.
        """
        fsync_parents([item.planned.target for item in self.promoted])

    def rollback(self) -> None:
        """Undo the promotion after the owning commit failed.

        Each promoted target is removed and its quarantined predecessor renamed
        back, so the problem ends byte-identical to its pre-Save state. A
        half-promoted artifact — quarantined but not yet replaced — is restored
        too, because :meth:`promote` recorded it before attempting the rename.
        """
        for item in reversed(self.promoted):
            restore_quarantined(
                item.planned.target,
                item.quarantine,
                item.planned.root,
                target_existed=item.quarantine is not None,
            )
        self._flush_renames()
        self.promoted.clear()
        self.planned.clear()
        self.planned_removals.clear()

    def finish(self) -> None:
        """Drop the quarantined predecessors after a successful commit."""
        for item in self.promoted:
            if item.quarantine is None:
                continue
            try:
                require_inside(item.quarantine, item.planned.root)
            except PackageStagingError:
                continue
            remove_path(item.quarantine)
        self._flush_renames()
        self.promoted.clear()
        self.planned.clear()
        self.planned_removals.clear()


def restore_quarantined(
    target: Path,
    quarantine: Path | None,
    root: Path,
    *,
    target_existed: bool,
) -> bool:
    """Put ``quarantine`` back at ``target``, removing what replaced it.

    Both paths are re-validated against ``root`` first, so a corrupted or
    tampered journal can never direct a delete or a restore outside the
    configured storage roots.

    The quarantine present on disk is the authority, because it is proof that the
    displacement happened; a recorded expectation can be stale by the time
    recovery reads it. When no quarantine is on disk the two remaining cases are
    told apart by ``target_existed``:

    * the target had no previous content, so what is there now was promoted over
      nothing and is removed;
    * the target *did* have content and no quarantine exists, which means the
      displacement never completed and ``target`` still holds the original.
      It is left untouched — deleting it there would destroy exactly what this
      function protects, and keeping content the rows may not describe is the
      strictly safer half of that trade.

    Args:
        target: The promoted location to undo.
        quarantine: Where the predecessor was parked, when one was planned.
        root: The configured root both paths must stay inside.
        target_existed: Whether the target held content before the promotion.

    Returns:
        ``True`` when anything on disk was changed.
    """
    try:
        require_inside(target, root)
        if quarantine is not None:
            require_inside(quarantine, root)
    except PackageStagingError:
        return False
    if quarantine is not None and quarantine.exists():
        remove_path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        quarantine.replace(target)
        return True
    if target_existed:
        return False
    changed = target.exists()
    remove_path(target)
    return changed
