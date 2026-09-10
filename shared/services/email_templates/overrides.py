#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""A host-managed directory of template overrides, read without a deployment.

The operator publishes ``<root>/<namespace>/<key>.toml`` and the running process
picks it up. Two properties decide the design:

*Startup fails closed, runtime fails soft.* Every file in the module's namespace
is validated before the process serves traffic, because that check is the one
every replica performs identically on the same tree. Afterwards a file can change
under a live process at any moment, and refusing to send email because an operator
saved a typo would turn an editing mistake into an outage -- so a runtime update
that does not validate is logged and the last known-good version keeps sending.

*The retained version is per process.* Replicas can therefore disagree until the
file is fixed: each holds whatever it last read successfully, and a replica that
starts during the bad window will not start at all. That is the price of not
serializing template edits through a database, and it is why publication is
documented as an atomic rename of a sibling file.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from shared.services.email_templates.catalogue import EmailTemplate, EmailTemplateDefinition
from shared.services.email_templates.loader import load_email_template, validate_email_template

logger = logging.getLogger(__name__)

TEMPLATE_SUFFIX = ".toml"


@dataclass(frozen=True)
class _FileSignature:
    """The stat fields a change is detected from, without reading the file."""

    mtime_ns: int
    size: int
    inode: int

    @classmethod
    def of(cls, path: Path) -> _FileSignature | None:
        """Return the signature of ``path``, or ``None`` when it does not exist.

        Only an absent file returns ``None``. Every other stat failure -- a
        permission change, an I/O error, a directory that became a file -- is
        raised, because "gone" and "unreadable" mean opposite things here: the
        first is how an operator reverts to the packaged default, and the second
        must leave what is already sending alone.

        Raises:
            OSError: If the path exists but cannot be stat-ed.
        """
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return cls(mtime_ns=stat.st_mtime_ns, size=stat.st_size, inode=stat.st_ino)


@dataclass(frozen=True)
class OverrideState:
    """What one catalogue key is currently rendering from, and why.

    Attributes:
        key: The catalogue key this state describes.
        path: Where the override for the key would be read from.
        active: Whether a valid override is in use instead of the packaged default.
        error: The validation error retained from the last failed read, if any.
        based_on: Digest of the packaged default recorded by the active override,
            or ``None`` when it has no baseline metadata.
        stale: Whether ``based_on`` names a digest the packaged default no longer has.
    """

    key: str
    path: Path
    active: bool
    error: str | None = None
    based_on: str | None = None
    stale: bool = False


@dataclass(frozen=True)
class OverrideProblem:
    """One reason a tree is not fit to start from."""

    path: Path
    reason: str

    def __str__(self) -> str:
        """Render the problem as ``filename: reason`` for logs and the CLI."""
        return f"{self.path.name}: {self.reason}"


def default_digest(definition: EmailTemplateDefinition) -> str:
    """Return the digest an override records in ``based_on``.

    The digest covers the default's subject and body rather than the file's raw
    bytes, so reformatting a packaged TOML file -- rewrapping a multi-line string,
    reordering its two fields -- does not mark every override in the field stale.

    Args:
        definition: The catalogue entry whose packaged default is digested.

    Returns:
        The short SHA-256 hex digest of the default's subject and body.

    Raises:
        EmailTemplateError: If the packaged default cannot be read.
    """
    return _digest_of_default(definition.default_path)


@cache
def _digest_of_default(path: Path) -> str:
    """Digest one packaged default, memoized per path.

    The defaults ship inside the image and cannot change under a running process,
    so this is read once no matter how often staleness is checked. The cache is
    keyed on the path because a definition holds a mapping and is not hashable.
    """
    template = load_email_template(path)
    payload = f"{template.subject}\n{template.body}".encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _without_host_path(exc: Exception, path: Path) -> str:
    """Return an error message naming the file rather than its place on the host.

    The retained message is shown to administrators on the visibility pages,
    where the container's directory layout is noise at best. The log keeps the
    full path: that reader is on the host and needs it.
    """
    message = str(exc)
    for absolute in (str(path), getattr(exc, "filename", None)):
        if absolute:
            message = message.replace(str(absolute), path.name)
    return message


class FilesystemOverrideSource:
    """Serve template overrides from a namespaced directory, cached by stat.

    The source is consulted on every render, so it must be cheap: it stats one
    file per render and only reparses when mtime, size, or inode changed.
    """

    def __init__(
        self,
        root: Path,
        namespace: str,
        definitions: Iterable[EmailTemplateDefinition],
    ) -> None:
        """Bind a source to one module's namespace under an override root.

        Args:
            root: Directory holding one subdirectory per module namespace.
            namespace: Subdirectory name, ``web`` or ``arena``.
            definitions: The module catalogue entries overrides may replace.
        """
        self._directory = root / namespace
        self._definitions = {definition.key: definition for definition in definitions}
        self._signatures: dict[str, _FileSignature] = {}
        self._templates: dict[str, EmailTemplate] = {}
        self._errors: dict[str, str] = {}
        self._stale: set[str] = set()

    @property
    def directory(self) -> Path:
        """Expose the namespaced directory this source reads."""
        return self._directory

    def path_for(self, key: str) -> Path:
        """Return the file an override for ``key`` is read from."""
        return self._directory / f"{key}{TEMPLATE_SUFFIX}"

    def get(self, definition: EmailTemplateDefinition) -> EmailTemplate | None:
        """Return the current override for ``definition``, or ``None``.

        A file that disappeared drops back to the packaged default; a file that
        changed is reparsed and validated as one subject/body unit before it
        replaces the retained version.

        Args:
            definition: The catalogue entry being rendered.

        Returns:
            The override to render, or ``None`` to use the packaged default.
        """
        key = definition.key
        path = self.path_for(key)
        try:
            signature = _FileSignature.of(path)
        except OSError as exc:
            return self._retain_through(key, path, exc)
        if signature is None:
            self._forget(key)
            return None
        if self._signatures.get(key) == signature:
            return self._templates.get(key)
        self._signatures[key] = signature
        self._adopt(definition, path)
        return self._templates.get(key)

    def _retain_through(self, key: str, path: Path, exc: OSError) -> EmailTemplate | None:
        """Keep sending what already works when the file cannot be examined.

        A permission change or an I/O error on the mount is not an instruction to
        revert wording. The signature is dropped so the file is reread once it is
        readable again.
        """
        self._signatures.pop(key, None)
        self._errors[key] = _without_host_path(exc, path)
        retained = "retaining last valid override" if key in self._templates else "using packaged default"
        logger.error(
            "email template override unreadable: key=%s path=%s reason=%s action=%s",
            key,
            path,
            exc,
            retained,
        )
        return self._templates.get(key)

    def _adopt(self, definition: EmailTemplateDefinition, path: Path) -> None:
        """Parse and validate a changed file, retaining the last valid version."""
        key = definition.key
        try:
            template = load_email_template(path)
            validate_email_template(template, definition)
        except ValueError as exc:
            # Deliberately not raised: the retained version (or the packaged
            # default, when there is none) keeps sending until the file is fixed.
            self._errors[key] = _without_host_path(exc, path)
            retained = "retaining last valid override" if key in self._templates else "using packaged default"
            logger.error(
                "email template override rejected: key=%s path=%s reason=%s action=%s",
                key,
                path,
                exc,
                retained,
            )
            return
        self._errors.pop(key, None)
        self._templates[key] = template
        self._note_staleness(definition, template)
        logger.info("email template override loaded: key=%s path=%s", key, path)

    def _note_staleness(self, definition: EmailTemplateDefinition, template: EmailTemplate) -> None:
        """Record whether an override was written against an older default."""
        key = definition.key
        if template.based_on is None or template.based_on == default_digest(definition):
            self._stale.discard(key)
            return
        self._stale.add(key)
        logger.warning(
            "email template override is stale: key=%s based_on=%s current=%s",
            key,
            template.based_on,
            default_digest(definition),
        )

    def _forget(self, key: str) -> None:
        """Drop every trace of a key whose file is gone."""
        self._signatures.pop(key, None)
        self._templates.pop(key, None)
        self._errors.pop(key, None)
        self._stale.discard(key)

    def describe(self) -> tuple[OverrideState, ...]:
        """Report per-key source and validation state for operator visibility."""
        return tuple(
            OverrideState(
                key=key,
                path=self.path_for(key),
                active=key in self._templates,
                error=self._errors.get(key),
                based_on=self._templates[key].based_on if key in self._templates else None,
                stale=key in self._stale,
            )
            for key in sorted(self._definitions)
        )


def iter_override_files(directory: Path) -> Iterator[Path]:
    """Yield the override files in one namespace directory, sorted by name.

    Raises:
        OSError: If the directory exists but cannot be listed. An absent
            directory yields nothing, because a module namespace nobody created
            is the normal state of an install that overrides only the other one.
    """
    try:
        entries = sorted(directory.iterdir())
    except FileNotFoundError:
        return
    for entry in entries:
        if entry.is_file() and entry.suffix == TEMPLATE_SUFFIX:
            yield entry


def validate_override_tree(
    root: Path,
    namespace: str,
    definitions: Iterable[EmailTemplateDefinition],
) -> tuple[OverrideProblem, ...]:
    """Validate every override file in one module's namespace.

    Args:
        root: Directory holding one subdirectory per module namespace.
        namespace: Subdirectory name, ``web`` or ``arena``.
        definitions: The module catalogue entries overrides may replace.

    Returns:
        Every problem found, in filename order. An empty tuple means the tree is
        fit to start from; an absent directory is not itself a problem, but a
        directory that exists and cannot be listed is -- a namespace the process
        cannot read is indistinguishable from an empty one at every later point,
        so it must refuse the start rather than quietly send packaged defaults.
    """
    directory = root / namespace
    by_key = {definition.key: definition for definition in definitions}
    problems: list[OverrideProblem] = []
    try:
        candidates = list(iter_override_files(directory))
    except OSError as exc:
        return (OverrideProblem(path=directory, reason=f"cannot read override directory: {exc}"),)
    for path in candidates:
        key = path.stem
        definition = by_key.get(key)
        if definition is None:
            problems.append(OverrideProblem(path=path, reason=f"unknown template key {key!r}"))
            continue
        try:
            template = load_email_template(path)
            validate_email_template(template, definition)
        except ValueError as exc:
            # An unreadable or unparsable file lands here too: `load_email_template`
            # turns the OSError into the same error type, so a permissions mistake on
            # a published file fails the deploy rather than silently sending defaults.
            problems.append(OverrideProblem(path=path, reason=str(exc)))
    return tuple(sorted(problems, key=lambda problem: problem.path.name))
