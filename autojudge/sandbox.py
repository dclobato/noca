#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Isolate sandbox management for the run phase.

Provides synchronous helpers (to run inside ThreadPoolExecutor) that:
- initialise and clean up the isolate box
- construct the isolate --run command with resource limits
- parse the isolate meta file into a structured IsolateMeta result
- read kernel-recorded peak PID counts from the cgroup filesystem

All functions operate on a live Container object and are intended to be
dispatched via asyncio.get_running_loop().run_in_executor().
"""

import json
import logging
import shlex
from typing import cast

from docker.models.containers import Container

from autojudge.config import settings
from autojudge.languages import SANDBOX_DIR, STDERR_PATH, STDOUT_PATH, LanguageConfig
from autojudge.types import IsolateError, IsolateMeta, ProblemLimits

logger = logging.getLogger(__name__)

ISOLATE_META_PATH = f"{SANDBOX_DIR}/isolate-meta.txt"
ISOLATE_BOX_ID = 0
ISOLATE_CGROUP_PATH = f"/sys/fs/cgroup/box-{ISOLATE_BOX_ID}"
ISOLATE_PIDS_PEAK_PATH = f"{ISOLATE_CGROUP_PATH}/pids.peak"


def _sync_isolate_init(container: Container) -> None:
    """
    Initialise the isolate box inside a run container.

    Args:
        container: Live pool container.

    Raises:
        IsolateError: If isolate --init returns a non-zero exit code.
    """
    result = container.exec_run(_isolate_base_cmd() + ["--init"], user="root")
    if result.exit_code != 0:
        output = (result.output or b"").decode(errors="replace")
        raise IsolateError(f"isolate --init failed: {output}")


def _sync_isolate_cleanup(container: Container) -> None:
    """
    Clean up the isolate box inside a run container.

    Args:
        container: Live pool container.
    """
    container.exec_run(_isolate_base_cmd() + ["--cleanup"], user="root")


def _sync_reset_run_artifacts(container: Container) -> None:
    """
    Remove stale stdout, stderr and meta files before a new run.

    Args:
        container: Live pool container.

    Raises:
        IsolateError: If the rm command fails.
    """
    quoted_paths = " ".join(shlex.quote(path) for path in (STDOUT_PATH, STDERR_PATH, ISOLATE_META_PATH))
    result = container.exec_run(["sh", "-c", f"rm -f {quoted_paths}"], user="root")
    if result.exit_code != 0:
        output = (result.output or b"").decode(errors="replace")
        raise IsolateError(f"Failed to reset run artifacts: {output}")


def _sync_run_isolate(
    container: Container,
    language: LanguageConfig,
    limits: ProblemLimits,
    cpu_limit_s: float,
    wall_limit_s: float,
    output_limit_bytes: int,
) -> int:
    """
    Execute isolate --run inside the container.

    Args:
        container: Live pool container.
        language: Language configuration used to build run_cmd and extra dirs.
        limits: Problem resource limits (memory, pids).
        cpu_limit_s: CPU time limit in seconds.
        wall_limit_s: Wall-clock time limit in seconds.
        output_limit_bytes: Maximum stdout file size in bytes.

    Returns:
        The exit code returned by the isolate process.
    """
    cmd = _isolate_base_cmd() + [
        f"--meta={ISOLATE_META_PATH}",
        f"--stdin={SANDBOX_DIR}/input",
        f"--stdout={STDOUT_PATH}",
        f"--stderr={STDERR_PATH}",
        f"--time={cpu_limit_s:.3f}",
        f"--wall-time={wall_limit_s:.3f}",
        f"--cg-mem={limits.memory_limit_kb}",
        f"--processes={limits.pids_limit}",
        f"--fsize={max(1, (output_limit_bytes + 1023) // 1024)}",
        f"--dir={SANDBOX_DIR}={SANDBOX_DIR}:rw",
    ]
    cmd.extend(_runtime_isolate_dirs(language))
    cmd.extend(
        [
            f"--chdir={SANDBOX_DIR}",
            "--run",
            "--",
            *language.run_cmd,
        ]
    )
    result = container.exec_run(cmd, user="root", demux=False)
    return cast(int, result.exit_code)


def _read_isolate_cgroup_peak_pids(container: Container) -> int | None:
    """
    Read the kernel-recorded peak PID count for the active isolate box.

    Args:
        container: Live pool container.

    Returns:
        Peak PID count, or None if the file is missing or unparseable.
    """
    result = container.exec_run(
        ["sh", "-c", f"cat {shlex.quote(ISOLATE_PIDS_PEAK_PATH)}"],
        user="root",
    )
    if result.exit_code != 0:
        return None

    raw_value = (result.output or b"").decode(errors="replace").strip()
    if not raw_value:
        return None

    try:
        return int(raw_value)
    except ValueError:
        logger.warning("Invalid isolate pids.peak value")
        logger.warning(json.dumps({"path": ISOLATE_PIDS_PEAK_PATH, "value": raw_value}, indent=2))
        return None


def _resolve_peak_pids(meta_peak_pids: int | None, cgroup_peak_pids: int | None) -> int | None:
    """Prefer isolate meta, but fall back to the kernel cgroup peak when needed."""
    if meta_peak_pids is not None:
        return meta_peak_pids
    return cgroup_peak_pids


def _isolate_base_cmd() -> list[str]:
    """Return the base isolate command with box ID and cgroup flags."""
    return [
        settings.ISOLATE_BINARY_PATH,
        f"--box-id={ISOLATE_BOX_ID}",
        "--cg",
        "--silent",
    ]


def _runtime_isolate_dirs(language: LanguageConfig) -> list[str]:
    """Return extra read-only directory bindings needed by runtime-based languages."""
    if language.id in {"python3", "javascript", "c-sharp"}:
        return [
            "--dir=/etc=/etc",
            "--dir=/lib=/lib",
            "--dir=/lib64=/lib64",
            "--dir=/usr=/usr",
        ]

    if language.id in {"java", "kotlin"}:
        return [
            "--dir=/etc=/etc",
            "--dir=/lib=/lib",
            "--dir=/lib64=/lib64",
            "--dir=/usr=/usr",
            "--dir=/opt=/opt",
        ]

    return []


def _parse_isolate_meta(meta_text: str, *, isolate_exit_code: int) -> IsolateMeta:
    """
    Parse the isolate meta file text into an IsolateMeta struct.

    Args:
        meta_text: Raw text content of the meta file.
        isolate_exit_code: The exit code returned by the isolate process itself.

    Returns:
        Parsed IsolateMeta.

    Raises:
        IsolateError: If isolate reported an internal error or required fields are absent.
    """
    raw: dict[str, str] = {}
    for line in meta_text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        raw[key.strip()] = value.strip()

    status = raw.get("status")
    if isolate_exit_code > 1 or status == "XX":
        raise IsolateError(raw.get("message") or "isolate reported an internal error")

    if "time-wall" not in raw and status != "XX":
        raise IsolateError("isolate meta file is missing time-wall")

    if "exitcode" not in raw and "exitsig" not in raw and status not in {None, "TO"}:
        raise IsolateError("isolate meta file is missing both exitcode and exitsig")

    wall_time_ms = _seconds_text_to_ms(raw.get("time-wall"))
    cpu_time_ms = _seconds_text_to_ms(raw.get("time"))

    memory_kb = None
    if "cg-mem" in raw:
        memory_kb = _parse_int_field(raw["cg-mem"], field_name="cg-mem")
    elif "max-rss" in raw:
        memory_kb = _parse_int_field(raw["max-rss"], field_name="max-rss")

    peak_pids = None
    for field_name in ("cg-pids", "max-processes"):
        if field_name in raw:
            peak_pids = _parse_int_field(raw[field_name], field_name=field_name)
            break

    exit_code = _parse_optional_int_field(raw.get("exitcode"), field_name="exitcode")
    exit_signal = _parse_optional_int_field(raw.get("exitsig"), field_name="exitsig")

    return IsolateMeta(
        status=status,
        exit_code=exit_code,
        exit_signal=exit_signal,
        wall_time_ms=wall_time_ms,
        cpu_time_ms=cpu_time_ms,
        memory_kb=memory_kb,
        peak_pids=peak_pids,
        cg_oom_killed="cg-oom-killed" in raw,
    )


def _seconds_text_to_ms(value: str | None) -> int | None:
    """Convert an isolate seconds string (e.g. '0.123') to milliseconds."""
    if value is None or value == "":
        return None
    try:
        return int(float(value) * 1000)
    except ValueError as exc:
        raise IsolateError(f"Invalid isolate time value: {value!r}") from exc


def _parse_optional_int_field(value: str | None, *, field_name: str) -> int | None:
    """Parse an optional integer field from the isolate meta file."""
    if value is None or value == "":
        return None
    return _parse_int_field(value, field_name=field_name)


def _parse_int_field(value: str, *, field_name: str) -> int:
    """Parse a required integer field from the isolate meta file."""
    try:
        return int(value)
    except ValueError as exc:
        raise IsolateError(f"Invalid isolate {field_name} value: {value!r}") from exc
