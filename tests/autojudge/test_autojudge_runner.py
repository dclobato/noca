"""Unit tests for autojudge.runner helpers."""

from __future__ import annotations

import pytest

from autojudge.runner import (
    IsolateError,
    _parse_isolate_meta,
    _resolve_peak_pids,
    _runtime_isolate_dirs,
)
from shared.language_registry import default_language_configs


def test_parse_isolate_meta_success() -> None:
    meta = _parse_isolate_meta(
        "\n".join(
            [
                "time:0.123",
                "time-wall:0.456",
                "cg-mem:2048",
                "exitcode:0",
            ]
        ),
        isolate_exit_code=0,
    )

    assert meta.wall_time_ms == 456
    assert meta.cpu_time_ms == 123
    assert meta.memory_kb == 2048
    assert meta.exit_code == 0
    assert meta.status is None


def test_parse_isolate_meta_requires_wall_time() -> None:
    with pytest.raises(IsolateError, match="time-wall"):
        _parse_isolate_meta(
            "\n".join(
                [
                    "time:0.123",
                    "exitcode:0",
                ]
            ),
            isolate_exit_code=0,
        )


def test_parse_isolate_meta_raises_for_internal_failure() -> None:
    with pytest.raises(IsolateError, match="internal"):
        _parse_isolate_meta(
            "status:XX\nmessage:isolate internal error",
            isolate_exit_code=2,
        )


def test_resolve_peak_pids_prefers_meta_value() -> None:
    assert _resolve_peak_pids(7, 8) == 7


def test_resolve_peak_pids_falls_back_to_cgroup_value() -> None:
    assert _resolve_peak_pids(None, 8) == 8


def test_runtime_isolate_dirs_include_dynamic_loader_paths_for_python() -> None:
    python = next(language for language in default_language_configs() if language.id == "python3")

    dirs = _runtime_isolate_dirs(python)

    assert "--dir=/etc=/etc" in dirs
    assert "--dir=/lib=/lib" in dirs
    assert "--dir=/lib64=/lib64" in dirs
    assert "--dir=/usr=/usr" in dirs
