#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Smoke test the autojudge compile + run pipeline against sample_question fixtures.

This is a worker-layer smoke test only:
- no PostgreSQL
- no Valkey
- no web routes

It exercises the real compile containers, run containers, pool warmup, and the
current `isolate`-backed `run_test_case()` path using the files in
`sample_question/`.

Usage:
    uv run scripts/autojudge/smoke_test_judge.py
    uv run scripts/autojudge/smoke_test_judge.py --language python3
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import docker

sys.path.insert(0, str(Path(__file__).parents[2]))

from autojudge.config import settings
from autojudge.image_sync import _canonical_image_ref
from autojudge.pool import ContainerPool
from autojudge.runner import ProblemLimits, SubmissionSource, compile_submission, run_test_case
from autojudge.verdict import CaseResult, aggregate_verdict, worst_resource_usage
from shared.enumerations import Verdict
from shared.language_registry import LanguageConfig, default_language_registry, get_language

REPO_ROOT = Path(__file__).parents[2]
SAMPLE_DIR = REPO_ROOT / "sample_question"
INPUT_DIR = SAMPLE_DIR / "in"
OUTPUT_DIR = SAMPLE_DIR / "out"

SOURCE_FILES: dict[str, Path] = {
    "gcc-c17": SAMPLE_DIR / "main.c",
    "gcc-cpp23": SAMPLE_DIR / "main.cpp",
    "python3": SAMPLE_DIR / "main.py",
    "java": SAMPLE_DIR / "main.java",
    "javascript": SAMPLE_DIR / "main.js",
    "kotlin": SAMPLE_DIR / "main.kt",
    "fpc-pascal": SAMPLE_DIR / "main.pas",
    "go": SAMPLE_DIR / "main.go",
    "rust": SAMPLE_DIR / "main.rs",
    "c-sharp": SAMPLE_DIR / "main.cs",
    "haskell": SAMPLE_DIR / "main.hs",
    "lua": SAMPLE_DIR / "main.lua",
    "prolog": SAMPLE_DIR / "main.pl",
    "fortran": SAMPLE_DIR / "main.f90",
    "swift": SAMPLE_DIR / "main.swift",
    "ruby": SAMPLE_DIR / "main.rb",
    "bash": SAMPLE_DIR / "main.bash",
}

DEFAULT_LANGUAGES = list(SOURCE_FILES.keys())


def _apply_image_registry(language: LanguageConfig) -> LanguageConfig:
    """Override image refs using IMAGE_REGISTRY/IMAGE_NAMING when configured."""
    if not settings.IMAGE_REGISTRY:
        return language
    from dataclasses import replace

    return replace(
        language,
        compile_image=_canonical_image_ref(language.id, "compile"),
        run_image=_canonical_image_ref(language.id, "run"),
    )


PROBLEM_LIMITS = ProblemLimits(
    time_limit_ms=2000,
    memory_limit_kb=262144,
    pids_limit=64,
    output_limit_in_bytes=1024 * 1024,
)

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
CYAN = "\033[36m"
YELLOW = "\033[33m"


def _c(text: str, color: str) -> str:
    return f"{color}{text}{RESET}"


def _header(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{'─' * 68}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─' * 68}{RESET}")


def _load_test_cases() -> list[tuple[int, bytes, bytes]]:
    cases: list[tuple[int, bytes, bytes]] = []
    for in_path in sorted(INPUT_DIR.iterdir()):
        if not in_path.is_file():
            continue
        out_path = OUTPUT_DIR / in_path.name
        if not out_path.exists():
            raise FileNotFoundError(f"Missing expected output for {in_path.name}")
        cases.append((int(in_path.stem), in_path.read_bytes(), out_path.read_bytes()))
    if not cases:
        raise RuntimeError(f"No sample test cases found in {INPUT_DIR}")
    return cases


async def run_smoke_test(
    language_id: str,
    source_path: Path,
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor,
) -> bool:
    registry = default_language_registry()
    language = _apply_image_registry(get_language(registry, language_id))
    source_code = source_path.read_text(encoding="utf-8")
    cases = _load_test_cases()

    _header(f"NOCA Judge Smoke Test: {language.name} ({language_id})")
    print(f"  Source:      {source_path}")
    print(f"  Test cases:  {len(cases)}")
    print(f"  Run image:   {language.run_image}")
    print(f"  Compile img: {language.compile_image}")

    compile_start = time.monotonic()
    compile_result = await compile_submission(
        SubmissionSource(
            judgment_id=f"smoke-{language_id}-judgment",
            submission_id=f"smoke-{language_id}-submission",
            source_code=source_code,
        ),
        language,
        docker_client,
        executor,
    )
    compile_ms = int((time.monotonic() - compile_start) * 1000)
    if not compile_result.success:
        print(_c(f"  ✗ compile failed ({compile_ms} ms)", RED))
        if compile_result.compile_log:
            print(_c("  " + compile_result.compile_log.replace("\n", "\n  "), RED))
        return False

    print(_c(f"  ✓ compile ok ({compile_ms} ms)", GREEN))

    pool = ContainerPool(
        language=language,
        docker_client=docker_client,
        executor=executor,
        target_size=1,
    )
    warm_start = time.monotonic()
    await pool.warm()
    warm_ms = int((time.monotonic() - warm_start) * 1000)
    container_id = await pool.acquire()
    print(_c(f"  ✓ pool warm ok ({warm_ms} ms) [{container_id[:12]}]", GREEN))

    case_results: list[CaseResult] = []
    all_ok = True

    try:
        for ordinal, input_data, expected_output in cases:
            started = time.monotonic()
            run_result = await run_test_case(
                container_id=container_id,
                language=language,
                limits=PROBLEM_LIMITS,
                artifact_data=compile_result.artifact_data or b"",
                input_data=input_data,
                expected_output=expected_output,
                docker_client=docker_client,
                executor=executor,
            )
            elapsed_ms = int((time.monotonic() - started) * 1000)

            case_results.append(
                CaseResult(
                    verdict=run_result.verdict,
                    wall_time_ms=run_result.wall_time_ms,
                    memory_kb=run_result.memory_kb,
                    exit_code=run_result.exit_code,
                    stdout_excerpt=run_result.stdout_excerpt,
                    stderr_excerpt=run_result.stderr_excerpt,
                )
            )

            ok = run_result.verdict == Verdict.AC
            color = GREEN if ok else RED
            symbol = "✓" if ok else "✗"
            print(
                f"  {_c(symbol, color)} case {ordinal:02d} "
                f"{_c(run_result.verdict.value, color):<10} "
                f"wall={str(run_result.wall_time_ms):>5} ms "
                f"mem={str(run_result.memory_kb):>7} KB "
                f"real={elapsed_ms:>5} ms"
            )

            if not ok:
                all_ok = False
                print(_c(f"    exit_code: {run_result.exit_code!r}", YELLOW))
                if run_result.stdout_excerpt:
                    print(_c(f"    stdout: {run_result.stdout_excerpt.decode(errors='replace')!r}", YELLOW))
                if run_result.stderr_excerpt:
                    print(_c(f"    stderr: {run_result.stderr_excerpt.decode(errors='replace')!r}", YELLOW))
                remaining = len(cases) - ordinal
                if remaining:
                    print(_c(f"  stopping after first non-AC ({remaining} cases skipped)", YELLOW))
                break
    finally:
        await pool._kill_and_remove(container_id)
        await pool.shutdown()

    final = aggregate_verdict(case_results)
    peaks = worst_resource_usage(case_results)
    color = GREEN if final == Verdict.AC else RED
    print(
        f"\n  {_c(f'Final verdict: {final.value}', color + BOLD)} "
        f"(peak wall: {peaks['peak_wall_time_ms']} ms, peak mem: {peaks['peak_memory_kb']} KB)"
    )
    return all_ok


async def main(languages: list[str]) -> int:
    print(f"\n{BOLD}NOCA Autojudge Smoke Test{RESET}")
    print(f"Languages to test: {', '.join(languages)}")
    print(f"Docker daemon: {settings.DOCKER_BASE_URL}")

    executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="docker-sdk")
    docker_client = docker.DockerClient(base_url=settings.DOCKER_BASE_URL, timeout=60)

    results: dict[str, bool] = {}
    try:
        for language_id in languages:
            source_path = SOURCE_FILES.get(language_id)
            if source_path is None or not source_path.exists():
                print(_c(f"\n  Missing sample source for {language_id}: {source_path}", RED))
                results[language_id] = False
                continue
            try:
                results[language_id] = await run_smoke_test(language_id, source_path, docker_client, executor)
            except Exception as exc:
                print(_c(f"\n  EXCEPTION for {language_id}: {exc}", RED))
                import traceback

                traceback.print_exc()
                results[language_id] = False
    finally:
        docker_client.close()
        executor.shutdown(wait=False)

    print(f"\n{BOLD}{'─' * 68}{RESET}")
    print(f"{BOLD}Summary{RESET}")
    all_passed = True
    for language_id, ok in results.items():
        status = _c("✓ PASS", GREEN + BOLD) if ok else _c("✗ FAIL", RED + BOLD)
        print(f"  {status}  {language_id}")
        all_passed = all_passed and ok
    return 0 if all_passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NOCA autojudge smoke test")
    parser.add_argument(
        "--language",
        "-l",
        choices=[*DEFAULT_LANGUAGES, "all"],
        default="all",
        help="Which language to test",
    )
    args = parser.parse_args()
    selected = DEFAULT_LANGUAGES if args.language == "all" else [args.language]
    raise SystemExit(asyncio.run(main(selected)))
