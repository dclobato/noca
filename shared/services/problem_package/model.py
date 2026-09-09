#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The frozen contract both problem domains consume.

Payloads are :class:`~pathlib.Path` references into a staging area rather than
bytes, so only bounded metadata and already-DB-resident strings ever live in
memory. Staging ownership stays *outside* these values: a live resource handle
never sits inside a frozen value object, so the reader returns a separate
:class:`StagedPackage` holding both.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from shared.enumerations import ArenaEditorialReleasePolicy, ProblemValidatorType

if TYPE_CHECKING:
    from shared.services.custom_validator import PackagedValidator
    from shared.services.problem_package.errors import PackageWarning
    from shared.services.problem_package.staging import PackageStagingArea
    from shared.services.sample_interactions import PackagedInteraction


@dataclass(frozen=True, slots=True)
class PackageLanguageLimit:
    """Per-language limit overrides read from ``problem.json``.

    ``output_limit_in_bytes`` may be ``None``, meaning the language inherits the
    problem's own limit — matching the retained nullability of
    ``problem_language_limits.output_limit_in_bytes``. ``repetitions`` may be
    ``None``, meaning the importing domain applies its language registry's
    profiling default, which only that domain knows.
    """

    time_limit_ms: int
    memory_limit_kb: int
    pids_limit: int
    output_limit_in_bytes: int | None
    repetitions: int | None


@dataclass(frozen=True, slots=True)
class ValidatorSpec:
    """The ``custom_validator`` object as declared in ``problem.json``."""

    language_id: str
    source_file: str


@dataclass(frozen=True, slots=True)
class EditorialSpec:
    """The optional ``editorial`` object declared in ``problem.json``."""

    member: str
    sha256: str


@dataclass(frozen=True, slots=True)
class PackageMetadata:
    """Every ``problem.json`` key, already validated, normalized, and defaulted.

    Keys a given domain cannot store are still parsed and still exported: the
    package format is the union of both domains, and dropping a field on the way
    through is what made the two exporters diverge in the first place.

    ``validator_type`` is always populated regardless of the package's own
    version: version 2 states it, and a version-1 package has it derived from
    ``custom_validator`` presence by the parser. Consumers therefore never branch
    on the version to learn the strategy.

    ``expected_difficulty`` is the author's declared difficulty on the internal
    ``[1, 100]`` rating scale, or ``None`` for no estimate. Arena-only and
    additive within version 2: absent means no estimate.

    ``editorial_release_policy`` is flat here while the JSON nests it inside the
    ``editorial`` object. The nesting is a wire-format choice -- the policy only
    means anything when an editorial exists -- but ``EditorialSpec`` is the
    integrity declaration, whose digest the *writer* computes, so an exporter
    could not fill one in without inventing a hash.
    """

    format_version: int
    validator_type: ProblemValidatorType
    title: str
    author: str | None
    notes: str | None
    source: str | None
    license: str | None
    color: str | None
    hide_author_show_source: bool
    statement_language: str | None
    time_limit_ms: int
    memory_limit_kb: int
    pids_limit: int
    output_limit_in_bytes: int
    categories: tuple[str, ...]
    collection: str | None
    sample_testcases: tuple[int, ...]
    image: str | None
    image_caption: str | None
    language_limits: Mapping[str, PackageLanguageLimit]
    custom_validator: ValidatorSpec | None
    sha256: Mapping[str, str]
    editorial: EditorialSpec | None = None
    editorial_release_policy: ArenaEditorialReleasePolicy | None = None
    expected_difficulty: int | None = None


@dataclass(frozen=True, slots=True)
class PackageStatement:
    """The problem statement carried by a package.

    A reader always fills ``path``. An exporter whose domain keeps the statement
    in the database (Arena) instead fills ``text`` alone, so the writer never has
    to be handed a file that does not exist.

    Attributes:
        kind: ``"md"`` or ``"pdf"``.
        path: Staged file holding the statement bytes, or ``None``.
        text: Decoded Markdown text, ``None`` for a PDF statement.
    """

    kind: Literal["md", "pdf"]
    path: Path | None
    text: str | None


@dataclass(frozen=True, slots=True)
class PackageTestCase:
    """One test case, remapped to a contiguous 1-based ordinal.

    ``output_path`` is ``None`` for an interactive problem's case, which carries
    input only: the input parametrizes the validator, which decides the verdict.
    """

    ordinal: int
    is_sample: bool
    input_path: Path
    output_path: Path | None
    explanation: str | None


@dataclass(frozen=True, slots=True)
class PackageImage:
    """The packaged illustration image.

    As with the statement, a reader fills ``path`` while an exporter whose domain
    stores the image in the database fills ``data``.
    """

    member: str
    path: Path | None
    mime: str
    data: bytes | None = None


@dataclass(frozen=True, slots=True)
class ProblemPackage:
    """A fully validated problem package.

    This is also the canonical comparison unit for round-trip tests: raw ZIP
    bytes carry timestamps, hashes, and compression details that make byte
    equality meaningless.
    """

    metadata: PackageMetadata
    statement: PackageStatement
    test_cases: tuple[PackageTestCase, ...]
    image: PackageImage | None
    validator: PackagedValidator | None
    interactions: tuple[PackagedInteraction, ...]
    warnings: tuple[PackageWarning, ...]
    editorial: str | None = None

    @property
    def is_interactive(self) -> bool:
        """Whether the package's stored validation strategy is interactive.

        This reads the normalized strategy, never ``validator is not None``: an
        interactive problem whose validator source was removed is still
        interactive, and a standard problem carrying a stale validator row is
        still standard.
        """
        return self.metadata.validator_type is ProblemValidatorType.INTERACTIVE


@dataclass(frozen=True, slots=True)
class StagedPackage:
    """An immutable package plus the staging area holding its payloads.

    Leaving the reader's context manager removes every temporary path, which is
    why the handle lives here and not inside :class:`ProblemPackage`.
    """

    package: ProblemPackage
    staging: PackageStagingArea
