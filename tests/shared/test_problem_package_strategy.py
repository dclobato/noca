#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Format-version 2 strategy tests: the ``validator_type`` discriminator.

Version 1 carried no strategy, so it had to be inferred from validator presence
-- an inference wrong in both directions. These tests pin the version-2 contract
and the version-1 compatibility that reads the older packages unchanged.
"""

from __future__ import annotations

import dataclasses
import json
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from shared.enumerations import ProblemValidatorType
from shared.services.problem_package import PackageError, ProblemPackage, build_package, read_problem_package
from shared.services.problem_package.constants import FORMAT_VERSION
from tests.shared.test_problem_package import (  # reuse the package builders
    MINIMAL_STATEMENT,
    minimal_members,
    minimal_metadata,
    read,
    write_zip,
)

_VALIDATOR_SPEC = {"language_id": "python3", "source_file": "validator/validator.py"}


def _interactive_members(**extra: bytes | str) -> dict[str, bytes | str]:
    """Return the members an interactive package needs (no ``out/``)."""
    return {
        "statement.md": MINIMAL_STATEMENT,
        "in/001.in": "42 50\n",
        "validator/validator.py": "print('ready')\n",
        **extra,
    }


def _v2(validator_type: Any = "standard", **overrides: Any) -> dict[str, Any]:
    """Return a version-2 ``problem.json`` mapping, standard unless overridden."""
    return minimal_metadata(format_version=2, validator_type=validator_type, **overrides)


# ── Version 1 compatibility ───────────────────────────────────────────────────


@pytest.mark.parametrize("declared", [None, 1])
def test_v1_without_a_validator_reads_as_standard(tmp_path: Path, declared: int | None) -> None:
    """An absent version key and an explicit 1 are the same legacy format."""
    metadata = minimal_metadata() if declared is None else minimal_metadata(format_version=declared)

    package = read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=metadata))

    assert package.metadata.validator_type is ProblemValidatorType.STANDARD
    assert package.is_interactive is False


@pytest.mark.parametrize("declared", [None, 1])
def test_v1_with_a_validator_reads_as_interactive(tmp_path: Path, declared: int | None) -> None:
    overrides: dict[str, Any] = {"custom_validator": _VALIDATOR_SPEC}
    if declared is not None:
        overrides["format_version"] = declared

    package = read(write_zip(tmp_path / "p.zip", _interactive_members(), metadata=minimal_metadata(**overrides)))

    assert package.metadata.validator_type is ProblemValidatorType.INTERACTIVE
    assert package.is_interactive is True


@pytest.mark.parametrize("stray", ["interactive", "checker", "nonsense", 42])
def test_v1_ignores_a_stray_validator_type_key(tmp_path: Path, stray: object) -> None:
    """A v1 package's strategy comes from its validator, never from the key.

    The parser tolerates unrecognized keys everywhere else, so a key belonging to
    a later version is ignored rather than made into a new compatibility break --
    including a value that would be rejected outright under version 2.
    """
    metadata = minimal_metadata(format_version=1, validator_type=stray)

    package = read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=metadata))

    assert package.metadata.validator_type is ProblemValidatorType.STANDARD


# ── Version 2: the discriminator ──────────────────────────────────────────────


def test_v2_standard_round_trips(tmp_path: Path) -> None:
    package = read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=_v2()))

    assert package.metadata.format_version == 2
    assert package.metadata.validator_type is ProblemValidatorType.STANDARD


def test_v2_interactive_round_trips(tmp_path: Path) -> None:
    metadata = _v2(validator_type="interactive", custom_validator=_VALIDATOR_SPEC)

    package = read(write_zip(tmp_path / "p.zip", _interactive_members(), metadata=metadata))

    assert package.metadata.validator_type is ProblemValidatorType.INTERACTIVE
    assert package.validator is not None


def test_v2_without_a_strategy_is_refused(tmp_path: Path) -> None:
    metadata = minimal_metadata(format_version=2)

    with pytest.raises(PackageError, match="'validator_type' is required in format version 2"):
        read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=metadata))


@pytest.mark.parametrize("value", ["", "STANDARD", "token", "inter active"])
def test_v2_with_an_unknown_strategy_is_refused(tmp_path: Path, value: str) -> None:
    with pytest.raises(PackageError, match="unknown 'validator_type'"):
        read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=_v2(validator_type=value)))


@pytest.mark.parametrize("value", [1, True, ["standard"]])
def test_v2_with_a_mistyped_strategy_is_refused(tmp_path: Path, value: object) -> None:
    with pytest.raises(PackageError, match="'validator_type' must be a string"):
        read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=_v2(validator_type=value)))


def test_v2_accepts_a_padded_strategy(tmp_path: Path) -> None:
    """Leading and trailing whitespace is trimmed, matching every other string field."""
    package = read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=_v2(validator_type=" standard ")))

    assert package.metadata.validator_type is ProblemValidatorType.STANDARD


# ── Version 2: consistency between strategy, declaration, and members ─────────


def test_v2_standard_declaring_a_validator_is_refused(tmp_path: Path) -> None:
    metadata = _v2(custom_validator=_VALIDATOR_SPEC)

    with pytest.raises(PackageError, match="a 'standard' problem must declare 'custom_validator': null"):
        read(write_zip(tmp_path / "p.zip", _interactive_members(), metadata=metadata))


def test_v2_interactive_without_a_declaration_is_refused(tmp_path: Path) -> None:
    metadata = _v2(validator_type="interactive")

    with pytest.raises(PackageError, match="an 'interactive' problem must declare a 'custom_validator'"):
        read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=metadata))


def test_v2_standard_shipping_validator_members_is_refused(tmp_path: Path) -> None:
    """The archive-index half of the rule, which metadata parsing cannot see.

    ``custom_validator`` is null, so the metadata is self-consistent; only the
    extracted member list reveals that the package ships a validator anyway.
    """
    members = minimal_members(**{"validator/validator.py": "print('ready')\n"})

    with pytest.raises(PackageError, match="ships validator/ members but problem.json declares 'validator_type'"):
        read(write_zip(tmp_path / "p.zip", members, metadata=_v2()))


def test_v1_shipping_validator_members_keeps_its_own_message(tmp_path: Path) -> None:
    """A v1 package states no strategy, so the error must not invent one."""
    with pytest.raises(PackageError, match="declares no 'custom_validator'"):
        read(write_zip(tmp_path / "p.zip", _interactive_members(), metadata=minimal_metadata()))


# ── Output checker: reserved, never imported ──────────────────────────────────


def test_checker_is_refused_with_the_unsupported_message(tmp_path: Path) -> None:
    """Rejected centrally, before any ProblemPackage exists.

    Both domain importers therefore surface the identical error without either
    implementing the check, which is what keeps them from diverging on it.
    """
    with pytest.raises(PackageError, match="Output checker validation is not available in this build"):
        read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=_v2(validator_type="checker")))


def test_checker_is_refused_before_other_metadata_problems(tmp_path: Path) -> None:
    """The strategy is resolved before the rest of the mapping is interpreted."""
    metadata = {"format_version": 2, "validator_type": "checker", "title": "x" * 5000}

    with pytest.raises(PackageError, match="Output checker validation is not available in this build"):
        read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=metadata))


# ── Writing version 2 ─────────────────────────────────────────────────────────


def _written_metadata(destination: Path) -> dict[str, Any]:
    """Return the ``problem.json`` mapping a written package carries."""
    with zipfile.ZipFile(destination) as archive:
        return dict(json.loads(archive.read("problem.json")))


@pytest.mark.parametrize(
    ("members", "metadata", "expected"),
    [
        (minimal_members(), minimal_metadata(), "standard"),
        (_interactive_members(), minimal_metadata(custom_validator=_VALIDATOR_SPEC), "interactive"),
    ],
)
def test_a_v1_package_is_rewritten_at_the_current_version(
    tmp_path: Path,
    members: dict[str, bytes | str],
    metadata: dict[str, Any],
    expected: str,
) -> None:
    """Reading v1 and writing it back produces a current-version package with the derived strategy."""
    source = write_zip(tmp_path / "in.zip", members, metadata=metadata)
    with read_problem_package(source) as staged:
        destination = build_package(staged.package, tmp_path / "out.zip", profile="full")

    written = _written_metadata(destination)
    assert written["format_version"] == FORMAT_VERSION
    assert written["validator_type"] == expected
    # Reserved for the output-checker strategy, which has not landed.
    assert "checker_semantics" not in written


def test_a_v2_package_survives_a_read_write_read_cycle(tmp_path: Path) -> None:
    metadata = _v2(validator_type="interactive", custom_validator=_VALIDATOR_SPEC)
    source = write_zip(tmp_path / "in.zip", _interactive_members(), metadata=metadata)

    with read_problem_package(source) as staged:
        destination = build_package(staged.package, tmp_path / "out.zip", profile="full")
    reread = read(destination)

    assert reread.metadata.validator_type is ProblemValidatorType.INTERACTIVE
    assert reread.validator is not None


def test_a_v2_total_budget_is_converted_when_rewritten(tmp_path: Path) -> None:
    """A rewrite changes the version only after normalizing limit semantics."""
    metadata = _v2(
        language_limits={
            "python3": {
                "time_limit_ms": 3000,
                "memory_limit_kb": 262144,
                "pids_limit": 64,
                "repetitions": 3,
            }
        }
    )
    source = write_zip(tmp_path / "in.zip", minimal_members(), metadata=metadata)

    with read_problem_package(source) as staged:
        destination = build_package(staged.package, tmp_path / "out.zip", profile="full")

    written = _written_metadata(destination)
    assert written["format_version"] == FORMAT_VERSION
    assert written["language_limits"]["python3"]["time_limit_ms"] == 1000


def test_a_legacy_total_without_repetitions_cannot_be_relabelled_as_v3(tmp_path: Path) -> None:
    """Only an importing domain can resolve an omitted repetition count."""
    metadata = _v2(
        language_limits={
            "python3": {
                "time_limit_ms": 3000,
                "memory_limit_kb": 262144,
                "pids_limit": 64,
            }
        }
    )
    source = write_zip(tmp_path / "in.zip", minimal_members(), metadata=metadata)

    with (
        read_problem_package(source) as staged,
        pytest.raises(PackageError, match="has no repetition count"),
    ):
        build_package(staged.package, tmp_path / "out.zip", profile="full")


def test_a_public_bundle_carries_no_strategy(tmp_path: Path) -> None:
    """The public profile writes no problem.json, so it states no strategy."""
    source = write_zip(tmp_path / "in.zip", minimal_members(), metadata=_v2())
    with read_problem_package(source) as staged:
        destination = build_package(staged.package, tmp_path / "out.zip", profile="public")

    assert "problem.json" not in set(zipfile.ZipFile(destination).namelist())


# ── Refusing what version 2 cannot express ────────────────────────────────────


@contextmanager
def _interactive_package_without_source(tmp_path: Path) -> Iterator[ProblemPackage]:
    """Yield an interactive package whose validator source has been dropped.

    This is the state a problem reaches when its validator source is removed: it
    stays interactive and simply becomes non-judgeable. The package is yielded
    inside its staging context, because its payload paths stop existing as soon
    as that context ends.
    """
    metadata = _v2(validator_type="interactive", custom_validator=_VALIDATOR_SPEC)
    source = write_zip(tmp_path / "in.zip", _interactive_members(), metadata=metadata)
    with read_problem_package(source) as staged:
        package = staged.package
        yield ProblemPackage(
            metadata=package.metadata,
            statement=package.statement,
            test_cases=package.test_cases,
            image=package.image,
            validator=None,
            interactions=package.interactions,
            warnings=package.warnings,
        )


def test_a_full_export_of_a_sourceless_interactive_problem_is_refused(tmp_path: Path) -> None:
    with (
        _interactive_package_without_source(tmp_path) as package,
        pytest.raises(PackageError, match="has no validator source"),
    ):
        build_package(package, tmp_path / "out.zip", profile="full")


def test_require_importable_false_waives_the_completeness_rule(tmp_path: Path) -> None:
    """The contest backup exporter's escape hatch.

    A backup must never become impossible because one problem lost its validator
    source; the embedded package is not the restore source of record.
    """
    with _interactive_package_without_source(tmp_path) as package:
        destination = build_package(package, tmp_path / "out.zip", profile="full", require_importable=False)

    assert _written_metadata(destination)["validator_type"] == "interactive"


def test_a_full_export_of_a_checker_problem_is_refused(tmp_path: Path) -> None:
    """The writer refuses a strategy it has no representation for.

    A ``checker`` problem cannot be created through any application path today,
    so this guards the writer rather than a reachable flow: it is the one place
    that would otherwise emit an archive nothing can read.
    """
    source = write_zip(tmp_path / "in.zip", minimal_members(), metadata=_v2())
    with read_problem_package(source) as staged:
        package = staged.package
        checker = ProblemPackage(
            metadata=dataclasses.replace(
                package.metadata,
                validator_type=ProblemValidatorType.OUTPUT_CHECKER,
            ),
            statement=package.statement,
            test_cases=package.test_cases,
            image=package.image,
            validator=None,
            interactions=package.interactions,
            warnings=package.warnings,
        )

        with pytest.raises(PackageError, match="Output checker validation is not available in this build"):
            build_package(checker, tmp_path / "out.zip", profile="full")


# ── is_interactive reads the strategy, not the source ─────────────────────────


def test_is_interactive_follows_the_strategy_not_the_validator(tmp_path: Path) -> None:
    """An interactive package with no source is still interactive.

    This is the property the whole release exists to establish: validator
    presence answers "does it hold a revision", never "what kind of problem is
    this".
    """
    with _interactive_package_without_source(tmp_path) as package:
        assert package.validator is None
        assert package.is_interactive is True
