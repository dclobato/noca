#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Strict ``problem.json`` parsing: parse, then validate, then default.

Every coercion decision lives here rather than in a domain importer, so the two
domains cannot disagree about what a value means. In particular a JSON number is
never silently accepted where a string belongs, ``0`` is never confused with
"absent", and every rejection quotes the offending value.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from shared.services.problem_package.constants import (
    DEFAULT_MEMORY_LIMIT_KB,
    DEFAULT_OUTPUT_LIMIT_BYTES,
    DEFAULT_PIDS_LIMIT,
    DEFAULT_TIME_LIMIT_MS,
    FORMAT_VERSION,
    MAX_AUTHOR_CHARS,
    MAX_IMAGE_CAPTION_CHARS,
    MAX_LICENSE_CHARS,
    MAX_NOTES_CHARS,
    MAX_SOURCE_CHARS,
    MAX_TITLE_CHARS,
)
from shared.services.problem_package.errors import PackageError
from shared.services.problem_package.model import PackageLanguageLimit, PackageMetadata, ValidatorSpec

_LIMIT_DEFAULTS = {
    "time_limit_ms": DEFAULT_TIME_LIMIT_MS,
    "memory_limit_kb": DEFAULT_MEMORY_LIMIT_KB,
    "pids_limit": DEFAULT_PIDS_LIMIT,
    "output_limit_in_bytes": DEFAULT_OUTPUT_LIMIT_BYTES,
}


def decode_problem_json(raw: bytes) -> dict[str, Any]:
    """Decode ``problem.json`` bytes into a mapping.

    Raises:
        PackageError: If the bytes are not UTF-8 JSON holding an object.
    """
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise PackageError("problem.json is not valid UTF-8.") from exc
    except json.JSONDecodeError as exc:
        raise PackageError(f"problem.json is not valid JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise PackageError("problem.json must contain a JSON object.")
    return decoded


def check_format_version(meta: Mapping[str, Any]) -> int:
    """Validate ``format_version`` before any other field is looked at.

    An absent key means version 1, the format that predates the key. Any other
    value fails here rather than after half the metadata has been interpreted
    under assumptions the package never agreed to.

    Raises:
        PackageError: If the value is present and not the supported version.
    """
    if "format_version" not in meta:
        return FORMAT_VERSION
    raw = meta["format_version"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise PackageError(f"problem.json: 'format_version' must be an integer; got {raw!r}.")
    if raw != FORMAT_VERSION:
        raise PackageError(
            f"problem.json: unsupported 'format_version' {raw}; this build reads version {FORMAT_VERSION} only."
        )
    return raw


def parse_metadata(meta: Mapping[str, Any]) -> PackageMetadata:
    """Validate a decoded ``problem.json`` mapping into the frozen contract.

    Raises:
        PackageError: On any invalid, mistyped, or over-long recognized field.
    """
    version = check_format_version(meta)
    return PackageMetadata(
        format_version=version,
        title=_required_string(meta, "title", MAX_TITLE_CHARS),
        author=_string(meta, "author", MAX_AUTHOR_CHARS),
        notes=_string(meta, "notes", MAX_NOTES_CHARS),
        source=_string(meta, "source", MAX_SOURCE_CHARS),
        license=_string(meta, "license", MAX_LICENSE_CHARS),
        color=_color(meta.get("color")),
        hide_author_show_source=_bool(meta, "hide_author_show_source"),
        statement_language=_string(meta, "statement_language", 8),
        time_limit_ms=_positive_int(meta, "time_limit_ms", default=_LIMIT_DEFAULTS["time_limit_ms"]),
        memory_limit_kb=_positive_int(meta, "memory_limit_kb", default=_LIMIT_DEFAULTS["memory_limit_kb"]),
        pids_limit=_positive_int(meta, "pids_limit", default=_LIMIT_DEFAULTS["pids_limit"]),
        output_limit_in_bytes=_positive_int(
            meta, "output_limit_in_bytes", default=_LIMIT_DEFAULTS["output_limit_in_bytes"]
        ),
        categories=_categories(_absent_or(meta, "categories")),
        sample_testcases=_sample_testcases(_absent_or(meta, "sample_testcases")),
        image=_string(meta, "image", 255),
        image_caption=_string(meta, "image_caption", MAX_IMAGE_CAPTION_CHARS),
        language_limits=_language_limits(_absent_or(meta, "language_limits")),
        custom_validator=_validator_spec(meta.get("custom_validator")),
        sha256=_sha256_map(_absent_or(meta, "sha256")),
    )


def _absent_or(meta: Mapping[str, Any], key: str) -> Any:
    """Return a non-nullable field's value, refusing an explicit ``null``.

    These fields have an empty value (``[]`` / ``{}``) that means what ``null``
    would be trying to say, so ``null`` is a statement the format cannot express
    and is reported rather than silently read as absence.
    """
    if key not in meta:
        return None
    value = meta[key]
    if value is None:
        raise PackageError(f"problem.json: '{key}' must not be null; omit it or use an empty value.")
    return value


def _required_string(meta: Mapping[str, Any], key: str, max_chars: int) -> str:
    """Read a mandatory string field.

    Raises:
        PackageError: If the field is absent, blank, mistyped, or over-long.
    """
    value = _string(meta, key, max_chars)
    if value is None:
        raise PackageError(f"problem.json: '{key}' is required.")
    return value


def _string(meta: Mapping[str, Any], key: str, max_chars: int) -> str | None:
    """Type-check, trim, and length-check an optional string field."""
    value = meta.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise PackageError(f"problem.json: '{key}' must be a string; got {value!r}.")
    trimmed = value.strip()
    if not trimmed:
        return None
    if len(trimmed) > max_chars:
        raise PackageError(f"problem.json: '{key}' must be at most {max_chars} characters; got {len(trimmed)}.")
    return trimmed


def _bool(meta: Mapping[str, Any], key: str) -> bool:
    """Read a strict boolean field, defaulting to ``False`` when absent.

    An explicit ``null`` is rejected rather than treated as absent: this field
    cannot be null, so a package stating it as null is stating something the
    format cannot express.
    """
    if key not in meta:
        return False
    value = meta[key]
    if not isinstance(value, bool):
        raise PackageError(f"problem.json: '{key}' must be a boolean; got {value!r}.")
    return value


def _positive_int(meta: Mapping[str, Any], key: str, *, default: int) -> int:
    """Read an integer ``>= 1``, accepting a trimmed decimal string.

    An explicit ``null`` is rejected rather than treated as absent: a package
    that states a limit as null is stating something the format cannot express.
    """
    if key not in meta:
        return default
    value = meta[key]
    if value is None:
        raise PackageError(f"problem.json: '{key}' must be an integer >= 1; got null.")
    if isinstance(value, bool):
        raise PackageError(f"problem.json: '{key}' must be an integer >= 1; got {value!r}.")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().lstrip("+-").isdigit():
        parsed = int(value.strip())
    else:
        raise PackageError(f"problem.json: '{key}' must be an integer >= 1; got {value!r}.")
    if parsed < 1:
        raise PackageError(f"problem.json: '{key}' must be an integer >= 1; got {parsed}.")
    return parsed


def _optional_positive_int(value: Any, label: str) -> int | None:
    """Read an integer ``>= 1`` that may legitimately be omitted or null."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise PackageError(f"problem.json: {label} must be an integer >= 1; got {value!r}.")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().lstrip("+-").isdigit():
        parsed = int(value.strip())
    else:
        raise PackageError(f"problem.json: {label} must be an integer >= 1; got {value!r}.")
    if parsed < 1:
        raise PackageError(f"problem.json: {label} must be an integer >= 1; got {parsed}.")
    return parsed


def _color(value: Any) -> str | None:
    """Validate an optional Contest balloon color."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise PackageError(f"problem.json: 'color' must be a string; got {value!r}.")
    trimmed = value.strip()
    if not trimmed:
        return None
    if len(trimmed) != 7 or not trimmed.startswith("#") or not _is_hex(trimmed[1:]):
        raise PackageError(f"problem.json: 'color' must be a '#rrggbb' hex color; got {trimmed!r}.")
    return trimmed


def _is_hex(text: str) -> bool:
    """Return whether every character is a hexadecimal digit."""
    return all(character in "0123456789abcdefABCDEF" for character in text)


def _categories(value: Any) -> tuple[str, ...]:
    """Validate the category name list, preserving its sequence."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise PackageError(f"problem.json: 'categories' must be an array; got {value!r}.")
    names: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            raise PackageError(f"problem.json: every category must be a string; got {entry!r}.")
        trimmed = entry.strip()
        if trimmed and trimmed not in names:
            names.append(trimmed)
    return tuple(names)


def _sample_testcases(value: Any) -> tuple[int, ...]:
    """Validate ``sample_testcases`` as unique, strictly ascending ordinals."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise PackageError(f"problem.json: 'sample_testcases' must be an array; got {value!r}.")
    ordinals: list[int] = []
    for entry in value:
        if isinstance(entry, bool) or not isinstance(entry, int) or entry < 1:
            raise PackageError(f"problem.json: 'sample_testcases' entries must be integers >= 1; got {entry!r}.")
        if ordinals and entry <= ordinals[-1]:
            raise PackageError(
                "problem.json: 'sample_testcases' must be strictly ascending and unique; "
                f"{entry} follows {ordinals[-1]}."
            )
        ordinals.append(entry)
    return tuple(ordinals)


def _language_limits(value: Any) -> Mapping[str, PackageLanguageLimit]:
    """Validate the per-language override map."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PackageError(f"problem.json: 'language_limits' must be an object; got {value!r}.")
    limits: dict[str, PackageLanguageLimit] = {}
    for language_id, entry in value.items():
        if not isinstance(entry, dict):
            raise PackageError(f"problem.json: language_limits[{language_id!r}] must be an object.")
        limits[str(language_id)] = PackageLanguageLimit(
            time_limit_ms=_required_positive(entry, "time_limit_ms", language_id),
            memory_limit_kb=_required_positive(entry, "memory_limit_kb", language_id),
            pids_limit=_required_positive(entry, "pids_limit", language_id),
            # Omitted or null means "inherit the problem's limit", matching the
            # retained nullability of problem_language_limits.output_limit_in_bytes.
            output_limit_in_bytes=_optional_positive_int(
                entry.get("output_limit_in_bytes"), f"language_limits[{language_id!r}].output_limit_in_bytes"
            ),
            # Absent means "use the target's registry default", which only the
            # importing domain knows.
            repetitions=_optional_positive_int(
                entry.get("repetitions"), f"language_limits[{language_id!r}].repetitions"
            ),
        )
    return limits


def _required_positive(entry: Mapping[str, Any], key: str, language_id: object) -> int:
    """Read a mandatory positive integer from one language-limit entry."""
    parsed = _optional_positive_int(entry.get(key), f"language_limits[{language_id!r}].{key}")
    if parsed is None:
        raise PackageError(f"problem.json: language_limits[{language_id!r}] is missing '{key}'.")
    return parsed


def _validator_spec(value: Any) -> ValidatorSpec | None:
    """Validate the ``custom_validator`` object's shape."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise PackageError(f"problem.json: 'custom_validator' must be an object; got {value!r}.")
    language_id = value.get("language_id")
    source_file = value.get("source_file")
    if not isinstance(language_id, str) or not language_id.strip():
        raise PackageError("problem.json: custom validator 'language_id' is required.")
    if not isinstance(source_file, str) or not source_file.strip():
        raise PackageError("problem.json: custom validator 'source_file' is required.")
    return ValidatorSpec(language_id=language_id.strip(), source_file=source_file.strip())


def _sha256_map(value: Any) -> Mapping[str, str]:
    """Validate the optional integrity manifest's shape."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PackageError(f"problem.json: 'sha256' must be an object; got {value!r}.")
    digests: dict[str, str] = {}
    for member, digest in value.items():
        if not isinstance(member, str) or not isinstance(digest, str):
            raise PackageError("problem.json: 'sha256' must map member names to hex digest strings.")
        normalized = digest.strip().lower()
        if len(normalized) != 64 or not _is_hex(normalized):
            raise PackageError(f"problem.json: sha256[{member!r}] is not a 64-character hex digest.")
        digests[member] = normalized
    return digests
