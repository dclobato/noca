#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Shared language registry.

Public API re-exported from sub-modules for backward-compatible imports:
  - LanguageConfig, path constants, default configs/registry/seed rows → language_configs
  - default_stub_for_language_id → language_stubs

Registry logic (registry_from_rows, language_config_from_row, get_language, …)
and the highlightjs/ace-mode mappings live here.
"""

from __future__ import annotations

import pathlib
from typing import Any, Literal, overload

from shared.language_configs import (
    BINARY_PATH,
    DOTNET_PATH,
    INPUT_PATH,
    JAR_PATH,
    JAVA_PATH,
    JS_PATH,
    LUA_PATH,
    LUAC_PATH,
    NODE_PATH,
    PYTHON3_PATH,
    SANDBOX_DIR,
    STDERR_PATH,
    STDOUT_PATH,
    SWIPL_PATH,
    LanguageConfig,
    default_language_configs,
    default_language_registry,
    default_language_seed_rows,
)
from shared.language_stubs import default_stub_for_language_id

__all__ = [
    "BINARY_PATH",
    "DOTNET_PATH",
    "INPUT_PATH",
    "JAR_PATH",
    "JAVA_PATH",
    "JS_PATH",
    "LUA_PATH",
    "LUAC_PATH",
    "NODE_PATH",
    "PYTHON3_PATH",
    "SANDBOX_DIR",
    "STDERR_PATH",
    "STDOUT_PATH",
    "SWIPL_PATH",
    "LanguageConfig",
    "default_language_configs",
    "default_language_registry",
    "default_language_seed_rows",
    "default_stub_for_language_id",
    "registry_from_rows",
    "language_config_from_row",
    "get_language",
    "active_language_ids",
    "highlightjs_language_for_language_id",
    "highlightjs_languages_for_registry",
    "ace_mode_for_language_id",
    "ace_modes_for_registry",
]


def registry_from_rows(rows: list[dict[str, Any]]) -> dict[str, LanguageConfig]:
    registry: dict[str, LanguageConfig] = {}
    for row in rows:
        language = language_config_from_row(row)
        if language.id in registry:
            raise ValueError(f"Duplicate language id '{language.id}' in database")
        registry[language.id] = language
    if not registry:
        raise ValueError("No languages found in database")
    return registry


def language_config_from_row(row: dict[str, Any]) -> LanguageConfig:
    language_id = _require_str(row, "id")
    artifact_is_source = _require_bool(row, "artifact_is_source")
    profiling_repetitions_default = _require_int(
        row,
        "profiling_repetitions_default",
        default=3 if artifact_is_source else 10,
        minimum=1,
    )
    source_filename = _require_str(row, "source_filename")
    return LanguageConfig(
        id=language_id,
        name=_require_str(row, "name"),
        icon=_require_str(row, "icon"),
        highlightjs_language=highlightjs_language_for_language_id(language_id),
        ace_mode=ace_mode_for_language_id(language_id),
        compile_image=_require_str(row, "compile_image"),
        run_image=_require_str(row, "run_image"),
        compile_cmd=_require_command_list(row.get("compile_cmd"), "compile_cmd", allow_none=True),
        run_cmd=_require_command_list(row.get("run_cmd"), "run_cmd"),
        source_filename=source_filename,
        default_extension=pathlib.Path(source_filename).suffix,
        artifact_path=_require_str(row, "artifact_path"),
        artifact_is_source=artifact_is_source,
        compile_timeout_s=_require_float(row, "compile_timeout_s"),
        profiling_repetitions_default=profiling_repetitions_default,
        profiled_pids_floor=_require_int(row, "profiled_pids_floor", default=32, minimum=1),
        version=_optional_str(row, "version"),
    )


def get_language(registry: dict[str, LanguageConfig], language_id: str) -> LanguageConfig:
    try:
        return registry[language_id]
    except KeyError as exc:
        available = ", ".join(sorted(registry.keys()))
        raise KeyError(f"Language '{language_id}' is not registered. Available languages: {available}") from exc


def active_language_ids(registry: dict[str, LanguageConfig]) -> list[str]:
    return list(registry.keys())


def highlightjs_language_for_language_id(language_id: str) -> str:
    mapping = {
        "gcc-c17": "c",
        "gcc-cpp23": "cpp",
        "python3": "python",
        "java": "java",
        "javascript": "javascript",
        "kotlin": "kotlin",
        "fpc-pascal": "delphi",
        "go": "go",
        "rust": "rust",
        "c-sharp": "csharp",
        "haskell": "haskell",
        "lua": "lua",
        "prolog": "prolog",
        "fortran": "fortran",
        "swift": "swift",
        "ruby": "ruby",
        "bash": "bash",
    }
    return mapping.get(language_id, "plaintext")


def highlightjs_languages_for_registry(registry: dict[str, LanguageConfig] | None = None) -> list[str]:
    if registry is None:
        registry = default_language_registry()
    return sorted({language.highlightjs_language for language in registry.values()})


def ace_mode_for_language_id(language_id: str) -> str:
    mapping = {
        "gcc-c17": "c_cpp",
        "gcc-cpp23": "c_cpp",
        "python3": "python",
        "java": "java",
        "javascript": "javascript",
        "kotlin": "kotlin",
        "fpc-pascal": "pascal",
        "go": "golang",
        "rust": "rust",
        "c-sharp": "csharp",
        "haskell": "haskell",
        "lua": "lua",
        "prolog": "prolog",
        "fortran": "fortran",
        "swift": "swift",
        "ruby": "ruby",
        "bash": "sh",
    }
    return mapping.get(language_id, "text")


def ace_modes_for_registry(registry: dict[str, LanguageConfig] | None = None) -> list[str]:
    if registry is None:
        registry = default_language_registry()
    return sorted({language.ace_mode for language in registry.values()})


def _require_str(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Language row field '{key}' must be a non-empty string")
    return value


def _optional_str(row: dict[str, Any], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Language row field '{key}' must be a string when present")
    return value or None


def _require_bool(row: dict[str, Any], key: str) -> bool:
    value = row.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"Language row field '{key}' must be a bool")
    return value


def _require_float(row: dict[str, Any], key: str) -> float:
    value = row.get(key)
    if not isinstance(value, int | float):
        raise ValueError(f"Language row field '{key}' must be numeric")
    return float(value)


def _require_int(row: dict[str, Any], key: str, *, default: int | None = None, minimum: int | None = None) -> int:
    value = row.get(key, default)
    if not isinstance(value, int):
        raise ValueError(f"Language row field '{key}' must be an int")
    if minimum is not None and value < minimum:
        raise ValueError(f"Language row field '{key}' must be >= {minimum}")
    return value


@overload
def _require_command_list(value: Any, field_name: str, *, allow_none: Literal[True]) -> list[str] | None: ...


@overload
def _require_command_list(value: Any, field_name: str, *, allow_none: Literal[False] = ...) -> list[str]: ...


def _require_command_list(
    value: Any,
    field_name: str,
    *,
    allow_none: bool = False,
) -> list[str] | None:
    if value is None:
        if allow_none:
            return None
        raise ValueError(f"Language row field '{field_name}' cannot be null")
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Language row field '{field_name}' must be a non-empty list[str]")
    return list(value)
