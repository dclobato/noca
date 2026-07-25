#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Generic, table-driven row and identifier validation primitives.

These helpers are the low-level building blocks the reference-graph checks in
:mod:`web.services.contest_backup_service.integrity` compose. They validate one
row against its Core table (columns, nullability, and per-type shape) and the
scalar identifier/reference invariants, with no knowledge of the backup graph.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, Table
from sqlalchemy import Enum as SAEnum

from .models import ContestBackupError
from .validation import ArchiveIndex


def index_rows(
    table: Table,
    rows: Iterable[dict[str, Any]],
    label: str,
    *,
    optional_columns: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Validate every row and index them by unique id."""
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        validated = validate_row(table, row, label, optional_columns=optional_columns)
        row_id = required_id(validated, label)
        if row_id in indexed:
            raise ContestBackupError(f"Duplicate {label} id: {row_id!r}.")
        indexed[row_id] = validated
    return indexed


def validate_row(
    table: Table,
    row: Mapping[str, Any],
    label: str,
    *,
    optional_columns: set[str] | None = None,
) -> dict[str, Any]:
    """Validate one row's columns, nullability, and per-type value shape."""
    optional = optional_columns or set()
    column_names = {column.name for column in table.columns}
    unknown = set(row) - column_names
    missing = column_names - set(row) - optional
    if unknown:
        raise ContestBackupError(f"{label.title()} contains unknown columns: {', '.join(sorted(unknown))}.")
    if missing:
        raise ContestBackupError(f"{label.title()} is missing columns: {', '.join(sorted(missing))}.")

    validated = dict(row)
    for column in table.columns:
        if column.name not in validated:
            continue
        value = validated[column.name]
        if value is None:
            if not column.nullable:
                raise ContestBackupError(f"{label.title()} column {column.name!r} cannot be null.")
            continue
        if isinstance(column.type, DateTime):
            if not isinstance(value, str):
                raise ContestBackupError(f"{label.title()} column {column.name!r} must be an ISO timestamp.")
            try:
                datetime.fromisoformat(value)
            except ValueError as exc:
                raise ContestBackupError(f"{label.title()} column {column.name!r} is not a valid timestamp.") from exc
        elif isinstance(column.type, SAEnum) and value not in column.type.enums:
            raise ContestBackupError(f"{label.title()} column {column.name!r} has an invalid enum value.")
        elif isinstance(column.type, Boolean) and not isinstance(value, bool):
            raise ContestBackupError(f"{label.title()} column {column.name!r} must be a boolean.")
        elif isinstance(column.type, Integer) and (not isinstance(value, int) or isinstance(value, bool)):
            raise ContestBackupError(f"{label.title()} column {column.name!r} must be an integer.")
        elif isinstance(column.type, String):
            if not isinstance(value, str):
                raise ContestBackupError(f"{label.title()} column {column.name!r} must be a string.")
            if column.type.length is not None and len(value) > column.type.length:
                raise ContestBackupError(f"{label.title()} column {column.name!r} is too long.")
    return validated


def validate_child(table: Table, row: dict[str, Any], judgment_id: str, label: str) -> dict[str, Any]:
    """Validate a judgment child row and confirm it links to ``judgment_id``."""
    child = validate_row(table, row, label)
    if child.get("judgment_id") != judgment_id:
        raise ContestBackupError(f"{label.title()} has a mismatched judgment id.")
    return child


def require_exact_keys(row: Mapping[str, Any], expected: set[str], label: str) -> None:
    """Reject an entry whose top-level keys are not exactly ``expected``."""
    if set(row) != expected:
        raise ContestBackupError(f"{label.title()} has an invalid object shape.")


def row_objects(value: Any, label: str) -> list[dict[str, Any]]:
    """Coerce a value to a list of JSON objects or raise."""
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ContestBackupError(f"{label} must be a JSON array of objects.")
    return value


def as_mapping(value: Any, label: str) -> dict[str, Any]:
    """Require a value to be a JSON object."""
    if not isinstance(value, dict):
        raise ContestBackupError(f"{label.title()} must be a JSON object.")
    return value


def required_id(row: Mapping[str, Any], label: str) -> str:
    """Return a non-empty string ``id`` or raise."""
    value = row.get("id")
    if not isinstance(value, str) or not value:
        raise ContestBackupError(f"{label.title()} has an invalid id.")
    return value


def positive_int(value: Any, label: str) -> int:
    """Return a strictly-positive integer or raise."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ContestBackupError(f"{label.title()} must be a positive integer.")
    return value


def require_reference(value: Any, target: Mapping[str, Any], label: str) -> str:
    """Require a string id that resolves inside ``target``."""
    if not isinstance(value, str) or value not in target:
        raise ContestBackupError(f"Dangling {label} reference: {value!r}.")
    return value


def validate_optional_reference(value: Any, target: Mapping[str, Any], label: str) -> None:
    """Validate an optional reference, allowing ``None``."""
    if value is not None:
        require_reference(value, target, label)


def validate_optional_user_reference(value: Any, users_by_id: Mapping[str, Any], label: str) -> None:
    """Validate an optional user reference, allowing ``None``."""
    validate_optional_reference(value, users_by_id, label)


def require_member(index: ArchiveIndex, name: str) -> None:
    """Require that a named payload member exists in the archive index."""
    if name not in index:
        raise ContestBackupError(f"Missing required problem payload: {name}.")


def require_unique_strings(values: Any, label: str) -> None:
    """Require a list of unique, non-empty strings."""
    if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
        raise ContestBackupError(f"Every {label} must be a non-empty string.")
    if len(set(values)) != len(values):
        raise ContestBackupError(f"Duplicate {label}.")
