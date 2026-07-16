#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Domain-neutral lifecycle helpers for interactive custom validators."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

from shared.enumerations import CustomValidatorActiveState, CustomValidatorCandidateState
from shared.language_configs import default_extension_for_language
from shared.queue_schema import CustomValidatorValidationJob

MAX_CUSTOM_VALIDATOR_SOURCE_BYTES = 256 * 1024
MAX_CUSTOM_VALIDATOR_COMPILE_LOG_CHARS = 16_384
VALIDATOR_PACKAGE_DIR = "validator"
ValidatorDomain = Literal["contest", "arena"]


def packaged_validator_member(language_id: str) -> str:
    """Return the archive member name for a validator written in ``language_id``.

    The name is ``validator/validator<ext>`` where ``<ext>`` is the language's
    source extension (e.g. ``validator/validator.py``). Packages record this in
    ``problem.json``'s ``custom_validator.source_file``.

    Args:
        language_id: The validator's language identifier.

    Returns:
        The safe, in-package member name for the validator source file.
    """
    return f"{VALIDATOR_PACKAGE_DIR}/validator{default_extension_for_language(language_id)}"


class ValidatorUploadError(ValueError):
    """Raised when an uploaded validator source violates the public contract."""


@dataclass(frozen=True)
class PackagedValidator:
    """Validated custom-validator metadata and source read from a package."""

    language_id: str
    source: str


def parse_packaged_validator(
    metadata: object,
    *,
    read_file: object,
    archive_names: set[str],
) -> PackagedValidator | None:
    """Validate custom-validator metadata and load its safe archive member.

    Args:
        metadata: Parsed ``problem.json.custom_validator`` value.
        read_file: Callable accepting an archive member name and returning bytes.
        archive_names: Exact archive member names.

    Returns:
        The packaged validator, or ``None`` when metadata is absent.

    Raises:
        ValidatorUploadError: If metadata, path, source, or visibility is invalid.
    """
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        raise ValidatorUploadError("problem.json: 'custom_validator' must be an object.")
    language_id = metadata.get("language_id")
    source_file = metadata.get("source_file")
    if not isinstance(language_id, str) or not language_id.strip():
        raise ValidatorUploadError("problem.json: custom validator language_id is required.")
    if not _is_safe_validator_member(source_file):
        raise ValidatorUploadError(
            "problem.json: custom validator source_file must be a safe file inside 'validator/'."
        )
    if source_file not in archive_names:
        raise ValidatorUploadError("Custom validator source file is missing from the package.")
    if not callable(read_file):
        raise TypeError("read_file must be callable")
    return PackagedValidator(language_id.strip(), parse_validator_source(read_file(source_file)))


class ValidatorRecord(Protocol):
    """Mutable fields required by the shared validator lifecycle."""

    active_language_id: str | None
    active_source: str | None
    active_state: CustomValidatorActiveState | None
    active_validated_at: datetime | None
    candidate_language_id: str | None
    candidate_source: str | None
    candidate_token: str | None
    candidate_state: CustomValidatorCandidateState | None
    candidate_compile_log: str | None
    candidate_validated_at: datetime | None


@dataclass(frozen=True)
class ValidatorStatusView:
    """Template-safe status for a custom validator configuration."""

    configured: bool
    usable: bool
    polling: bool
    active_language_id: str | None
    active_state: CustomValidatorActiveState | None
    candidate_language_id: str | None
    candidate_state: CustomValidatorCandidateState | None
    candidate_compile_log: str | None
    candidate_validated_at: datetime | None


@dataclass(frozen=True)
class CurrentValidatorSource:
    """The validator revision source authors can inspect or download."""

    source: str
    language_id: str


def parse_validator_source(upload: bytes) -> str:
    """Validate source size and UTF-8 encoding, then return decoded text.

    Args:
        upload: Exact uploaded file bytes.

    Returns:
        Decoded, non-empty validator source.

    Raises:
        ValidatorUploadError: If the source is empty, oversized, or invalid UTF-8.
    """
    if not upload:
        raise ValidatorUploadError("Validator source must not be empty.")
    if len(upload) > MAX_CUSTOM_VALIDATOR_SOURCE_BYTES:
        raise ValidatorUploadError("Validator source exceeds the 256 KiB limit.")
    try:
        source = upload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidatorUploadError("Validator source must be valid UTF-8.") from exc
    if not source.strip():
        raise ValidatorUploadError("Validator source must not be blank.")
    return source


def stage_candidate(record: ValidatorRecord, *, language_id: str, source: str) -> str:
    """Replace the staged candidate and return its fresh stale-job token."""
    token = str(uuid4())
    record.candidate_language_id = language_id
    record.candidate_source = source
    record.candidate_token = token
    record.candidate_state = CustomValidatorCandidateState.PENDING
    record.candidate_compile_log = None
    record.candidate_validated_at = None
    return token


def promote_candidate(record: ValidatorRecord, *, token: str) -> bool:
    """Atomically represent promotion when ``token`` still names the candidate."""
    if record.candidate_token != token or record.candidate_state != CustomValidatorCandidateState.PENDING:
        return False
    record.active_language_id = record.candidate_language_id
    record.active_source = record.candidate_source
    record.active_state = CustomValidatorActiveState.VALID
    record.active_validated_at = datetime.now(UTC)
    _clear_candidate(record)
    return True


def reject_candidate(record: ValidatorRecord, *, token: str, compile_log: str) -> bool:
    """Retain a matching invalid candidate without disturbing the active revision."""
    if record.candidate_token != token or record.candidate_state != CustomValidatorCandidateState.PENDING:
        return False
    record.candidate_state = CustomValidatorCandidateState.INVALID
    record.candidate_compile_log = compile_log[:MAX_CUSTOM_VALIDATOR_COMPILE_LOG_CHARS]
    record.candidate_validated_at = datetime.now(UTC)
    return True


def remove_validator(record: ValidatorRecord) -> None:
    """Clear active and candidate revisions immediately."""
    record.active_language_id = None
    record.active_source = None
    record.active_state = None
    record.active_validated_at = None
    _clear_candidate(record)


def build_validation_job(
    *, domain: ValidatorDomain, problem_id: str, candidate_token: str
) -> CustomValidatorValidationJob:
    """Build an immutable queue payload for a committed candidate."""
    return CustomValidatorValidationJob(
        validation_id=candidate_token,
        domain=domain,
        problem_id=problem_id,
        candidate_token=candidate_token,
    )


def status_view(record: ValidatorRecord | None) -> ValidatorStatusView:
    """Return the common UI and submission-gate view of a validator record."""
    if record is None:
        return ValidatorStatusView(False, True, False, None, None, None, None, None, None)
    configured = record.active_source is not None or record.candidate_source is not None
    usable = not configured or record.active_state == CustomValidatorActiveState.VALID
    return ValidatorStatusView(
        configured=configured,
        usable=usable,
        polling=record.candidate_state == CustomValidatorCandidateState.PENDING,
        active_language_id=record.active_language_id,
        active_state=record.active_state,
        candidate_language_id=record.candidate_language_id,
        candidate_state=record.candidate_state,
        candidate_compile_log=record.candidate_compile_log,
        candidate_validated_at=record.candidate_validated_at,
    )


def current_validator_source(record: ValidatorRecord | None) -> CurrentValidatorSource | None:
    """Return the current source, preferring the active revision over a candidate."""
    if record is None:
        return None
    if record.active_source is not None and record.active_language_id is not None:
        return CurrentValidatorSource(source=record.active_source, language_id=record.active_language_id)
    if record.candidate_source is not None and record.candidate_language_id is not None:
        return CurrentValidatorSource(source=record.candidate_source, language_id=record.candidate_language_id)
    return None


def _is_safe_validator_member(source_file: object) -> bool:
    """Return whether ``source_file`` is a safe ``validator/<basename>`` member.

    The path must be a string of the exact form ``validator/<name>`` where
    ``<name>`` is a single path segment with no directory separators and is
    neither ``.`` nor ``..``. This rejects traversal (e.g. ``../source.txt``)
    and nested paths while accepting both the legacy ``validator/source.txt``
    and language-named exports such as ``validator/validator.py``.
    """
    if not isinstance(source_file, str):
        return False
    prefix = f"{VALIDATOR_PACKAGE_DIR}/"
    if not source_file.startswith(prefix):
        return False
    name = source_file[len(prefix) :]
    return bool(name) and "/" not in name and name not in {".", ".."}


def _clear_candidate(record: ValidatorRecord) -> None:
    """Clear all candidate fields together to preserve table constraints."""
    record.candidate_language_id = None
    record.candidate_source = None
    record.candidate_token = None
    record.candidate_state = None
    record.candidate_compile_log = None
    record.candidate_validated_at = None
