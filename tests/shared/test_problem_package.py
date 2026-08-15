#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the shared problem-package reader, writer, and staging."""

from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from shared.services.problem_package import (
    DEFAULT_MEMORY_LIMIT_KB,
    DEFAULT_OUTPUT_LIMIT_BYTES,
    DEFAULT_PIDS_LIMIT,
    DEFAULT_TIME_LIMIT_MS,
    PackageError,
    ProblemPackage,
    build_package,
    read_problem_package,
)
from shared.services.problem_package.staging import STAGING_PREFIX
from shared.services.sample_problem_package import build_sample_problem_package

MINIMAL_STATEMENT = "# Title\n\nNo external links here.\n"


def write_zip(path: Path, members: Mapping[str, bytes | str], *, metadata: dict[str, Any] | None = None) -> Path:
    """Write a package ZIP from a member mapping, adding ``problem.json``."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        if metadata is not None:
            archive.writestr("problem.json", json.dumps(metadata))
        for name, content in members.items():
            archive.writestr(name, content)
    return path


def minimal_metadata(**overrides: Any) -> dict[str, Any]:
    """Return a valid minimal ``problem.json`` mapping."""
    return {"title": "Sample", **overrides}


def minimal_members(**extra: bytes | str) -> dict[str, bytes | str]:
    """Return the members every valid package needs."""
    return {"statement.md": MINIMAL_STATEMENT, "in/001.in": "1 2\n", "out/001.out": "3\n", **extra}


def read(path: Path) -> ProblemPackage:
    """Read a package and return it, with its staging area already released.

    Only metadata is inspected by callers of this helper; anything reading a
    staged payload must stay inside the context manager instead.
    """
    with read_problem_package(path) as staged:
        return staged.package


# ── Metadata: defaults, strictness, and widths ────────────────────────────────


def test_missing_limits_take_the_documented_defaults(tmp_path: Path) -> None:
    package = read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=minimal_metadata()))

    assert package.metadata.time_limit_ms == DEFAULT_TIME_LIMIT_MS
    assert package.metadata.memory_limit_kb == DEFAULT_MEMORY_LIMIT_KB
    assert package.metadata.pids_limit == DEFAULT_PIDS_LIMIT
    assert package.metadata.output_limit_in_bytes == DEFAULT_OUTPUT_LIMIT_BYTES


def test_missing_title_is_a_hard_error(tmp_path: Path) -> None:
    with pytest.raises(PackageError, match="'title' is required"):
        read(write_zip(tmp_path / "p.zip", minimal_members(), metadata={}))


def test_missing_statement_is_a_hard_error(tmp_path: Path) -> None:
    members = {"in/001.in": "1\n", "out/001.out": "1\n"}
    with pytest.raises(PackageError, match="statement.md or statement.pdf is required"):
        read(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata()))


def test_missing_test_cases_is_a_hard_error(tmp_path: Path) -> None:
    with pytest.raises(PackageError, match="No valid test cases"):
        read(write_zip(tmp_path / "p.zip", {"statement.md": MINIMAL_STATEMENT}, metadata=minimal_metadata()))


@pytest.mark.parametrize("value", [0, -1, 1.5, True, None, "abc", ""])
def test_non_positive_integer_limits_are_rejected(tmp_path: Path, value: object) -> None:
    metadata = minimal_metadata(time_limit_ms=value)
    with pytest.raises(PackageError, match="'time_limit_ms' must be an integer >= 1"):
        read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=metadata))


def test_decimal_string_limits_are_accepted(tmp_path: Path) -> None:
    package = read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=minimal_metadata(time_limit_ms=" 1500 ")))

    assert package.metadata.time_limit_ms == 1500


def test_a_number_where_a_string_belongs_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(PackageError, match="'notes' must be a string"):
        read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=minimal_metadata(notes=42)))


@pytest.mark.parametrize(
    ("field", "cap"),
    [("title", 256), ("author", 256), ("notes", 512), ("source", 256), ("license", 256), ("image_caption", 512)],
)
def test_unified_length_caps(tmp_path: Path, field: str, cap: int) -> None:
    metadata = minimal_metadata(**{field: "x" * (cap + 1)})
    with pytest.raises(PackageError, match=f"'{field}' must be at most {cap} characters"):
        read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=metadata))

    accepted = read(write_zip(tmp_path / "ok.zip", minimal_members(), metadata=minimal_metadata(**{field: "x" * cap})))
    assert len(getattr(accepted.metadata, field)) == cap


# ── format_version ────────────────────────────────────────────────────────────


def test_absent_format_version_means_one(tmp_path: Path) -> None:
    assert (
        read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=minimal_metadata())).metadata.format_version == 1
    )


@pytest.mark.parametrize("version", [0, 3, 99])
def test_unsupported_format_version_fails_before_other_metadata(tmp_path: Path, version: int) -> None:
    # The title is absent too; the version error must win, proving the check runs
    # before any other field is interpreted.
    metadata = {"format_version": version}
    with pytest.raises(PackageError, match="unsupported 'format_version'"):
        read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=metadata))


# ── sha256 manifest ───────────────────────────────────────────────────────────


def _digests(members: Mapping[str, bytes | str]) -> dict[str, str]:
    return {
        name: hashlib.sha256(content if isinstance(content, bytes) else content.encode("utf-8")).hexdigest()
        for name, content in members.items()
    }


def test_absent_manifest_warns_but_imports(tmp_path: Path) -> None:
    package = read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=minimal_metadata()))

    assert [warning.code for warning in package.warnings] == ["integrity_manifest_missing"]


def test_matching_manifest_produces_no_warning(tmp_path: Path) -> None:
    members = minimal_members()
    metadata = minimal_metadata(sha256=_digests(members))

    assert read(write_zip(tmp_path / "p.zip", members, metadata=metadata)).warnings == ()


def test_mismatched_manifest_is_rejected(tmp_path: Path) -> None:
    members = minimal_members()
    digests = _digests(members)
    digests["in/001.in"] = "0" * 64
    metadata = minimal_metadata(sha256=digests)

    with pytest.raises(PackageError, match="Integrity check failed for 'in/001.in'"):
        read(write_zip(tmp_path / "p.zip", members, metadata=metadata))


def test_manifest_must_not_cover_problem_json(tmp_path: Path) -> None:
    members = minimal_members()
    digests = _digests(members) | {"problem.json": "0" * 64}

    with pytest.raises(PackageError, match="must not cover 'problem.json'"):
        read(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata(sha256=digests)))


def test_incomplete_manifest_is_rejected(tmp_path: Path) -> None:
    members = minimal_members()
    digests = _digests(members)
    del digests["out/001.out"]

    with pytest.raises(PackageError, match="does not cover out/001.out"):
        read(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata(sha256=digests)))


def test_manifest_covers_raw_bytes_before_newline_normalization(tmp_path: Path) -> None:
    """A CRLF input hashes as shipped, not as normalized."""
    members = {"statement.md": MINIMAL_STATEMENT, "in/001.in": "1 2\r\n", "out/001.out": "3\n"}
    metadata = minimal_metadata(sha256=_digests(members))

    with read_problem_package(write_zip(tmp_path / "p.zip", members, metadata=metadata)) as staged:
        # The manifest matched (no error), and the staged file is normalized.
        assert staged.package.test_cases[0].input_path.read_bytes() == b"1 2\n"


# ── sample_testcases ──────────────────────────────────────────────────────────


def test_sample_testcases_marks_the_named_source_ordinals(tmp_path: Path) -> None:
    members = {
        "statement.md": MINIMAL_STATEMENT,
        "in/002.in": "a\n",
        "out/002.out": "a\n",
        "in/005.in": "b\n",
        "out/005.out": "b\n",
        "in/009.in": "c\n",
        "out/009.out": "c\n",
    }
    # Validation happens in the source ordinal space, before the contiguous remap.
    package = read(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata(sample_testcases=[2, 9])))

    assert [(case.ordinal, case.is_sample) for case in package.test_cases] == [(1, True), (2, False), (3, True)]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([1, 1], "strictly ascending and unique"),
        ([2, 1], "strictly ascending and unique"),
        ([0], "must be integers >= 1"),
        ([7], "has no test case for"),
    ],
)
def test_invalid_sample_testcases(tmp_path: Path, value: list[int], message: str) -> None:
    with pytest.raises(PackageError, match=message):
        read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=minimal_metadata(sample_testcases=value)))


def test_interactive_problem_may_not_declare_sample_testcases(tmp_path: Path) -> None:
    members = {
        "statement.md": MINIMAL_STATEMENT,
        "in/001.in": "1\n",
        "validator/validator.py": "print('ok')\n",
    }
    metadata = minimal_metadata(
        sample_testcases=[1],
        custom_validator={"language_id": "python3", "source_file": "validator/validator.py"},
    )
    with pytest.raises(PackageError, match="must be empty for an interactive problem"):
        read(write_zip(tmp_path / "p.zip", members, metadata=metadata))


# ── Ordinals ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("ordinal", [999, 1000])
def test_ordinals_within_range_are_accepted(tmp_path: Path, ordinal: int) -> None:
    members = {"statement.md": MINIMAL_STATEMENT, f"in/{ordinal}.in": "1\n", f"out/{ordinal}.out": "1\n"}

    assert len(read(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata())).test_cases) == 1


def test_ordinal_above_the_range_is_rejected_rather_than_ignored(tmp_path: Path) -> None:
    members = {
        "statement.md": MINIMAL_STATEMENT,
        "in/001.in": "1\n",
        "out/001.out": "1\n",
        "in/5000.in": "2\n",
        "out/5000.out": "2\n",
    }
    with pytest.raises(PackageError, match="outside the supported range"):
        read(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata()))


# ── Content rules ─────────────────────────────────────────────────────────────


def test_binary_test_case_is_rejected(tmp_path: Path) -> None:
    members = {"statement.md": MINIMAL_STATEMENT, "in/001.in": b"\xff\xfe\x00", "out/001.out": "3\n"}

    with pytest.raises(PackageError, match="not valid UTF-8 text; binary test cases"):
        read(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata()))


def test_empty_test_case_content_is_preserved(tmp_path: Path) -> None:
    members = {"statement.md": MINIMAL_STATEMENT, "in/001.in": "", "out/001.out": ""}

    with read_problem_package(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata())) as staged:
        case = staged.package.test_cases[0]
        assert case.input_path.read_bytes() == b""
        assert case.output_path is not None
        assert case.output_path.read_bytes() == b""


def test_unpaired_test_case_sides_are_rejected(tmp_path: Path) -> None:
    members = {"statement.md": MINIMAL_STATEMENT, "in/001.in": "1\n", "in/002.in": "2\n", "out/001.out": "1\n"}

    with pytest.raises(PackageError, match=r"Input files without matching output: ordinals \[2\]"):
        read(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata()))


def test_orphan_explanation_warns(tmp_path: Path) -> None:
    members = minimal_members(**{"explanation/007.txt": "orphan"})

    package = read(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata()))

    assert "orphan_explanation" in {warning.code for warning in package.warnings}


# ── Archive safety ────────────────────────────────────────────────────────────


def test_duplicate_member_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "p.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("problem.json", json.dumps(minimal_metadata()))
        archive.writestr("statement.md", MINIMAL_STATEMENT)
        archive.writestr("in/001.in", "1\n")
        archive.writestr("out/001.out", "1\n")
        with pytest.warns(UserWarning, match=r"Duplicate name: 'in/001\.in'"):
            archive.writestr("in/001.in", "2\n")

    with pytest.raises(PackageError, match="duplicate member"):
        read(path)


def test_case_folded_member_collision_is_rejected(tmp_path: Path) -> None:
    members = minimal_members(**{"Statement.MD": "# other\n"})

    with pytest.raises(PackageError, match="collide case- or Unicode-insensitively"):
        read(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata()))


@pytest.mark.parametrize("name", ["../escape.txt", "/absolute.txt", "back\\slash.txt", "double//sep.txt"])
def test_unsafe_member_names_are_rejected(tmp_path: Path, name: str) -> None:
    with pytest.raises(PackageError):
        read(write_zip(tmp_path / "p.zip", minimal_members(**{name: "x"}), metadata=minimal_metadata()))


def test_symlink_entry_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "p.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("problem.json", json.dumps(minimal_metadata()))
        archive.writestr("statement.md", MINIMAL_STATEMENT)
        archive.writestr("in/001.in", "1\n")
        archive.writestr("out/001.out", "1\n")
        info = zipfile.ZipInfo("link.txt")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/etc/passwd")

    with pytest.raises(PackageError, match="symbolic link"):
        read(path)


def _set_encrypted_flag(path: Path, member: str) -> None:
    """Flip the general-purpose encryption bit of one central-directory entry."""
    data = bytearray(path.read_bytes())
    needle = member.encode("utf-8")
    offset = 0
    while (offset := data.find(b"PK\x01\x02", offset)) != -1:
        name_length = int.from_bytes(data[offset + 28 : offset + 30], "little")
        if bytes(data[offset + 46 : offset + 46 + name_length]) == needle:
            data[offset + 8] |= 0x1
            path.write_bytes(bytes(data))
            return
        offset += 4
    raise AssertionError(f"member {member!r} not found in the central directory")


def test_encrypted_entry_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "p.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("problem.json", json.dumps(minimal_metadata()))
        archive.writestr("statement.md", MINIMAL_STATEMENT)
        archive.writestr("in/001.in", "1\n")
        archive.writestr("out/001.out", "1\n")
        archive.writestr("secret.txt", "ciphertext")
    # zipfile refuses to write the encryption bit itself, so set it directly in
    # the central-directory entry the reader actually inspects.
    _set_encrypted_flag(path, "secret.txt")

    with pytest.raises(PackageError, match="encrypted"):
        read(path)


def test_mixed_flat_and_directory_layouts_are_rejected(tmp_path: Path) -> None:
    members = {"statement.md": MINIMAL_STATEMENT, "in/001.in": "1\n", "out/001.out": "1\n", "002.in": "2\n"}

    with pytest.raises(PackageError, match="mixes flat"):
        read(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata()))


def test_out_and_sol_for_the_same_ordinal_are_rejected(tmp_path: Path) -> None:
    members = {"statement.md": MINIMAL_STATEMENT, "in/001.in": "1\n", "out/001.out": "1\n", "out/001.sol": "2\n"}

    with pytest.raises(PackageError, match="both provide the out stream"):
        read(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata()))


def test_macos_metadata_is_dropped_with_a_warning(tmp_path: Path) -> None:
    members = minimal_members(**{"__MACOSX/._statement.md": "junk", ".DS_Store": "junk"})

    package = read(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata()))

    assert "macos_metadata" in {warning.code for warning in package.warnings}


def test_unrecognized_safe_members_are_ignored(tmp_path: Path) -> None:
    members = minimal_members(**{"future/whatever.bin": b"\x00\x01"})

    assert read(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata())).metadata.title == "Sample"


# ── Validator and interactions ────────────────────────────────────────────────


def _interactive_members(**extra: bytes | str) -> dict[str, bytes | str]:
    return {
        "statement.md": MINIMAL_STATEMENT,
        "in/001.in": "42 50\n",
        "validator/validator.py": "print('ready')\n",
        **extra,
    }


_VALIDATOR_SPEC = {"language_id": "python3", "source_file": "validator/validator.py"}


def test_validator_members_without_metadata_are_a_hard_error(tmp_path: Path) -> None:
    with pytest.raises(PackageError, match="declares no 'custom_validator'"):
        read(write_zip(tmp_path / "p.zip", _interactive_members(), metadata=minimal_metadata()))


def test_interactive_package_ignores_output_files_with_a_warning(tmp_path: Path) -> None:
    members = _interactive_members(**{"out/001.out": "ignored\n"})
    metadata = minimal_metadata(custom_validator=_VALIDATOR_SPEC)

    package = read(write_zip(tmp_path / "p.zip", members, metadata=metadata))

    assert package.test_cases[0].output_path is None
    assert "ignored_interactive_output" in {warning.code for warning in package.warnings}


def test_validator_extension_mismatch_is_only_a_warning(tmp_path: Path) -> None:
    members = _interactive_members(**{"validator/validator.txt": "print('ready')\n"})
    metadata = minimal_metadata(custom_validator={"language_id": "python3", "source_file": "validator/validator.txt"})

    package = read(write_zip(tmp_path / "p.zip", members, metadata=metadata))

    assert package.validator is not None
    assert "validator_extension_mismatch" in {warning.code for warning in package.warnings}


def test_interactions_are_dropped_without_a_validator(tmp_path: Path) -> None:
    transcript = json.dumps({"lines": [{"dir": "validator", "line": "3"}], "truncated": False})
    members = minimal_members(**{"interaction/001.interaction": transcript})

    package = read(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata()))

    assert package.interactions == ()
    assert "interactions_dropped" in {warning.code for warning in package.warnings}


def test_interactions_are_read_alongside_a_validator(tmp_path: Path) -> None:
    transcript = json.dumps({"lines": [{"dir": "validator", "line": "3"}], "truncated": False})
    members = _interactive_members(**{"interaction/001.interaction": transcript, "interaction/001.explain": "why"})
    metadata = minimal_metadata(custom_validator=_VALIDATOR_SPEC)

    package = read(write_zip(tmp_path / "p.zip", members, metadata=metadata))

    assert len(package.interactions) == 1
    assert package.interactions[0].explanation == "why"


# ── PDF statements ────────────────────────────────────────────────────────────


def _pdf_bytes(*, pages: int = 1) -> bytes:
    """Build a small, valid PDF with ``pages`` pages."""
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_valid_pdf_statement_is_accepted(tmp_path: Path) -> None:
    members = {"statement.pdf": _pdf_bytes(), "in/001.in": "1\n", "out/001.out": "1\n"}

    assert read(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata())).statement.kind == "pdf"


def test_a_file_that_is_not_a_pdf_is_rejected(tmp_path: Path) -> None:
    members = {"statement.pdf": b"not a pdf at all", "in/001.in": "1\n", "out/001.out": "1\n"}

    with pytest.raises(PackageError, match="does not start with a PDF signature"):
        read(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata()))


def test_truncated_pdf_is_rejected(tmp_path: Path) -> None:
    members = {"statement.pdf": _pdf_bytes()[:40], "in/001.in": "1\n", "out/001.out": "1\n"}

    with pytest.raises(PackageError, match="Invalid statement.pdf"):
        read(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata()))


def test_encrypted_pdf_is_rejected(tmp_path: Path) -> None:
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    members = {"statement.pdf": buffer.getvalue(), "in/001.in": "1\n", "out/001.out": "1\n"}

    with pytest.raises(PackageError, match="encrypted PDF statements are not supported"):
        read(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata()))


def test_markdown_statement_wins_over_pdf(tmp_path: Path) -> None:
    members = {
        "statement.md": MINIMAL_STATEMENT,
        "statement.pdf": _pdf_bytes(),
        "in/001.in": "1\n",
        "out/001.out": "1\n",
    }

    assert read(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata())).statement.kind == "md"


# ── Writer ────────────────────────────────────────────────────────────────────


def test_full_export_writes_every_version_two_key(tmp_path: Path) -> None:
    source = build_sample_problem_package(tmp_path / "sample.zip")
    with read_problem_package(source) as staged:
        destination = build_package(staged.package, tmp_path / "out.zip", profile="full")

    metadata = json.loads(zipfile.ZipFile(destination).read("problem.json"))
    expected = {
        "format_version",
        "title",
        "author",
        "notes",
        "source",
        "license",
        "color",
        "hide_author_show_source",
        "statement_language",
        "validator_type",
        "time_limit_ms",
        "memory_limit_kb",
        "pids_limit",
        "output_limit_in_bytes",
        "categories",
        "sample_testcases",
        "image",
        "image_caption",
        "language_limits",
        "custom_validator",
        "sha256",
    }
    assert set(metadata) == expected
    assert "problem.json" not in metadata["sha256"]


def test_public_profile_carries_only_the_contestant_bundle(tmp_path: Path) -> None:
    source = build_sample_problem_package(tmp_path / "sample.zip")
    with read_problem_package(source) as staged:
        destination = build_package(staged.package, tmp_path / "public.zip", profile="public")

    names = set(zipfile.ZipFile(destination).namelist())
    assert "problem.json" not in names
    assert "statement.md" in names
    # Only the one public case (and its explanation) survives.
    assert {"in/001.in", "out/001.out", "explanation/001.txt"} <= names
    assert not any(name.startswith(("in/002", "out/002", "in/003", "out/003")) for name in names)


def test_export_fails_loudly_when_a_stored_file_is_missing(tmp_path: Path) -> None:
    source = build_sample_problem_package(tmp_path / "sample.zip")
    with read_problem_package(source) as staged:
        staged.package.test_cases[0].input_path.unlink()
        with pytest.raises(PackageError, match="the stored test case 1 input is missing"):
            build_package(staged.package, tmp_path / "out.zip", profile="full")


# ── Round trip ────────────────────────────────────────────────────────────────


def test_export_import_export_is_lossless(tmp_path: Path) -> None:
    """The canonical comparison unit is the parsed package, never raw ZIP bytes."""
    first = build_sample_problem_package(tmp_path / "one.zip")
    with read_problem_package(first) as staged:
        second = build_package(staged.package, tmp_path / "two.zip", profile="full")
        first_metadata = staged.package.metadata
        first_cases = [(case.ordinal, case.is_sample, case.explanation) for case in staged.package.test_cases]

    with read_problem_package(second) as staged:
        assert staged.package.metadata == first_metadata
        assert [(c.ordinal, c.is_sample, c.explanation) for c in staged.package.test_cases] == first_cases


# ── Staging cleanup ───────────────────────────────────────────────────────────


def test_staging_is_removed_on_success(tmp_path: Path) -> None:
    staging_parent = tmp_path / "scratch"
    source = build_sample_problem_package(tmp_path / "sample.zip")

    with read_problem_package(source, staging_parent=staging_parent) as staged:
        assert staged.staging.root.exists()

    assert not list(staging_parent.glob(f"{STAGING_PREFIX}*"))


def test_staging_is_removed_when_the_package_is_refused(tmp_path: Path) -> None:
    staging_parent = tmp_path / "scratch"
    bad = write_zip(tmp_path / "p.zip", {"statement.md": MINIMAL_STATEMENT}, metadata=minimal_metadata())

    with pytest.raises(PackageError), read_problem_package(bad, staging_parent=staging_parent) as _staged:
        pass  # pragma: no cover - the reader raises before the body runs

    assert not list(staging_parent.glob(f"{STAGING_PREFIX}*"))


# ── Regressions the review surfaced ───────────────────────────────────────────


def test_validator_source_file_must_live_inside_the_validator_directory(tmp_path: Path) -> None:
    """Otherwise 'statement.md' could be declared as the validator source."""
    metadata = minimal_metadata(custom_validator={"language_id": "python3", "source_file": "statement.md"})

    with pytest.raises(PackageError, match="must be a safe file inside 'validator/'"):
        read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=metadata))


def test_validator_source_file_may_not_traverse(tmp_path: Path) -> None:
    metadata = minimal_metadata(custom_validator={"language_id": "python3", "source_file": "validator/../secret"})

    with pytest.raises(PackageError, match="must be a safe file inside 'validator/'"):
        read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=metadata))


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("categories", "must not be null"),
        ("sample_testcases", "must not be null"),
        ("language_limits", "must not be null"),
        ("sha256", "must not be null"),
        ("hide_author_show_source", "must be a boolean"),
    ],
)
def test_an_explicit_null_is_not_the_same_as_an_absent_field(tmp_path: Path, field: str, message: str) -> None:
    """These fields have an empty value that says what null would be trying to."""
    metadata = minimal_metadata(**{field: None})

    with pytest.raises(PackageError, match=f"'{field}' {message}"):
        read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=metadata))


def test_a_null_format_version_is_rejected_rather_than_defaulted(tmp_path: Path) -> None:
    with pytest.raises(PackageError, match="'format_version' must be an integer"):
        read(write_zip(tmp_path / "p.zip", minimal_members(), metadata=minimal_metadata(format_version=None)))


def test_crlf_split_across_a_read_chunk_still_collapses(tmp_path: Path) -> None:
    """The streaming normalizer must not turn a split CRLF into two newlines."""
    from shared.services.problem_package import content

    chunk = content._NORMALIZE_CHUNK_BYTES
    # A CR as the very last byte of the first chunk, its LF as the first of the next.
    payload = ("a" * (chunk - 1)) + "\r\n" + "b\n"
    members = {"statement.md": MINIMAL_STATEMENT, "in/001.in": payload, "out/001.out": "1\n"}

    with read_problem_package(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata())) as staged:
        normalized = staged.package.test_cases[0].input_path.read_bytes()

    assert normalized == ("a" * (chunk - 1)).encode() + b"\nb\n"


def test_a_lone_trailing_cr_becomes_a_newline(tmp_path: Path) -> None:
    members = {"statement.md": MINIMAL_STATEMENT, "in/001.in": "1 2\r", "out/001.out": "3\n"}

    with read_problem_package(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata())) as staged:
        assert staged.package.test_cases[0].input_path.read_bytes() == b"1 2\n"


def test_an_oversized_interaction_transcript_is_rejected(tmp_path: Path) -> None:
    from shared.services.sample_interactions import MAX_INTERACTION_MEMBER_BYTES

    line = "x" * (MAX_INTERACTION_MEMBER_BYTES + 1024)
    transcript = json.dumps({"lines": [{"dir": "validator", "line": line}], "truncated": False})
    members = _interactive_members(**{"interaction/001.interaction": transcript})
    metadata = minimal_metadata(custom_validator=_VALIDATOR_SPEC)

    with pytest.raises(PackageError, match="the limit is"):
        read(write_zip(tmp_path / "p.zip", members, metadata=metadata))


def test_interaction_ordinals_agree_with_the_preflight(tmp_path: Path) -> None:
    """A member the preflight extracts must not then be silently discarded."""
    transcript = json.dumps({"lines": [{"dir": "validator", "line": "3"}], "truncated": False})
    members = _interactive_members(**{"interaction/1000.interaction": transcript})
    metadata = minimal_metadata(custom_validator=_VALIDATOR_SPEC)

    package = read(write_zip(tmp_path / "p.zip", members, metadata=metadata))

    assert len(package.interactions) == 1


@pytest.mark.parametrize("suffix", ["interaction", "explain"])
def test_interaction_ordinal_aliases_are_rejected(tmp_path: Path, suffix: str) -> None:
    transcript = json.dumps({"lines": [{"dir": "validator", "line": "3"}], "truncated": False})
    content = transcript if suffix == "interaction" else "explanation"
    members = _interactive_members(
        **{
            f"interaction/1.{suffix}": content,
            f"interaction/001.{suffix}": content,
        }
    )
    metadata = minimal_metadata(custom_validator=_VALIDATOR_SPEC)

    with pytest.raises(PackageError, match="both provide"):
        read(write_zip(tmp_path / "p.zip", members, metadata=metadata))


def test_explanation_ordinal_aliases_are_rejected(tmp_path: Path) -> None:
    members = minimal_members(
        **{
            "explanation/1.txt": "first",
            "explanation/001.txt": "second",
        }
    )

    with pytest.raises(PackageError, match="both provide"):
        read(write_zip(tmp_path / "p.zip", members, metadata=minimal_metadata()))


def test_round_trip_preserves_payload_bytes_not_only_metadata(tmp_path: Path) -> None:
    """The comparison covers the whole normalized package, payloads included."""
    first = build_sample_problem_package(tmp_path / "one.zip")

    with read_problem_package(first) as staged:
        before = _fingerprint(staged.package)
        second = build_package(staged.package, tmp_path / "two.zip", profile="full")

    with read_problem_package(second) as staged:
        assert _fingerprint(staged.package) == before


def _fingerprint(package: ProblemPackage) -> dict[str, object]:
    """Reduce a package to everything that must survive a round trip."""
    return {
        "metadata": package.metadata,
        "statement": (package.statement.kind, _payload(package.statement.path, package.statement.text)),
        "cases": [
            (
                case.ordinal,
                case.is_sample,
                case.explanation,
                case.input_path.read_bytes(),
                case.output_path.read_bytes() if case.output_path else None,
            )
            for case in package.test_cases
        ],
        "image": None if package.image is None else (package.image.member, package.image.mime),
        "validator": None if package.validator is None else (package.validator.language_id, package.validator.source),
        "interactions": [(item.transcript, item.explanation) for item in package.interactions],
    }


def _payload(path: Path | None, text: str | None) -> bytes:
    """Return a statement's bytes from wherever the package keeps them."""
    if path is not None:
        return path.read_bytes()
    return (text or "").encode("utf-8")
