#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from dataclasses import dataclass
from datetime import datetime

import pytest

from shared.enumerations import CustomValidatorActiveState, CustomValidatorCandidateState
from shared.services.custom_validator import (
    MAX_CUSTOM_VALIDATOR_COMPILE_LOG_CHARS,
    MAX_CUSTOM_VALIDATOR_SOURCE_BYTES,
    ValidatorUploadError,
    current_validator_source,
    parse_packaged_validator,
    parse_validator_source,
    promote_candidate,
    reject_candidate,
    remove_validator,
    stage_candidate,
    status_view,
)


@dataclass
class Record:
    id: str = "validator-id"
    active_language_id: str | None = None
    active_source: str | None = None
    active_state: CustomValidatorActiveState | None = None
    active_validated_at: datetime | None = None
    candidate_language_id: str | None = None
    candidate_source: str | None = None
    candidate_token: str | None = None
    candidate_state: CustomValidatorCandidateState | None = None
    candidate_compile_log: str | None = None
    candidate_validated_at: datetime | None = None


@pytest.mark.parametrize("upload", [b"", b" \n", b"\xff"])
def test_parse_validator_source_rejects_invalid_upload(upload: bytes) -> None:
    with pytest.raises(ValidatorUploadError):
        parse_validator_source(upload)


def test_parse_validator_source_enforces_byte_limit() -> None:
    with pytest.raises(ValidatorUploadError, match="256 KiB"):
        parse_validator_source(b"x" * (MAX_CUSTOM_VALIDATOR_SOURCE_BYTES + 1))


def test_replacement_promotes_only_matching_token() -> None:
    record = Record(active_language_id="old", active_source="old", active_state=CustomValidatorActiveState.VALID)
    token = stage_candidate(record, language_id="new", source="new")

    assert promote_candidate(record, token="stale") is False
    assert record.active_source == "old"
    assert promote_candidate(record, token=token) is True
    assert record.active_source == "new"
    assert record.candidate_source is None


def test_invalid_replacement_retains_active_revision_and_caps_log() -> None:
    record = Record(active_language_id="old", active_source="old", active_state=CustomValidatorActiveState.VALID)
    token = stage_candidate(record, language_id="new", source="broken")

    assert reject_candidate(record, token=token, compile_log="x" * 20_000)
    assert record.active_source == "old"
    assert record.candidate_state == CustomValidatorCandidateState.INVALID
    assert len(record.candidate_compile_log or "") == MAX_CUSTOM_VALIDATOR_COMPILE_LOG_CHARS
    assert status_view(record).usable is True


def test_runtime_failed_active_revision_remains_configured_but_unusable() -> None:
    record = Record(
        active_language_id="python3",
        active_source="print('validator')",
        active_state=CustomValidatorActiveState.RUNTIME_FAILED,
    )

    view = status_view(record)

    assert view.configured is True
    assert view.usable is False
    assert view.active_state == CustomValidatorActiveState.RUNTIME_FAILED


def test_current_validator_source_prefers_active_revision() -> None:
    record = Record(
        active_language_id="python3",
        active_source="print('active')\n",
        candidate_language_id="gcc-cpp23",
        candidate_source="int main() {}\n",
    )

    source = current_validator_source(record)

    assert source is not None
    assert source.language_id == "python3"
    assert source.source == "print('active')\n"


def test_current_validator_source_falls_back_to_candidate_revision() -> None:
    record = Record(
        candidate_language_id="gcc-cpp23",
        candidate_source="int main() {}\n",
    )

    source = current_validator_source(record)

    assert source is not None
    assert source.language_id == "gcc-cpp23"
    assert source.source == "int main() {}\n"


def test_current_validator_source_requires_complete_source_pair() -> None:
    record = Record(
        active_language_id="python3",
        candidate_source="print('candidate')\n",
    )

    assert current_validator_source(record) is None


def test_remove_clears_active_and_candidate_revisions() -> None:
    record = Record(active_language_id="old", active_source="old", active_state=CustomValidatorActiveState.VALID)
    stage_candidate(record, language_id="new", source="new")

    remove_validator(record)

    assert status_view(record).configured is False
    assert status_view(record).usable is True


@pytest.mark.parametrize(
    ("metadata", "names", "message"),
    [
        ("bad", set(), "must be an object"),
        ({"language_id": "python3", "source_file": "../source.txt"}, {"../source.txt"}, "source_file"),
        (
            {"language_id": "python3", "source_file": "validator/source.txt"},
            set(),
            "missing",
        ),
    ],
)
def test_parse_packaged_validator_rejects_malformed_metadata(
    metadata: object,
    names: set[str],
    message: str,
) -> None:
    with pytest.raises(ValidatorUploadError, match=message):
        parse_packaged_validator(metadata, read_file=lambda name: b"source", archive_names=names)


def test_parse_packaged_validator_enforces_source_contract() -> None:
    metadata = {"language_id": "python3", "source_file": "validator/source.txt"}
    with pytest.raises(ValidatorUploadError, match="256 KiB"):
        parse_packaged_validator(
            metadata,
            read_file=lambda name: b"x" * (MAX_CUSTOM_VALIDATOR_SOURCE_BYTES + 1),
            archive_names={"validator/source.txt"},
        )


def test_parse_packaged_validator_returns_utf8_source() -> None:
    packaged = parse_packaged_validator(
        {"language_id": "python3", "source_file": "validator/source.txt"},
        read_file=lambda name: "olá".encode(),
        archive_names={"validator/source.txt"},
    )
    assert packaged is not None
    assert packaged.language_id == "python3"
    assert packaged.source == "olá"
