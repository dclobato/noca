#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Probe isolate and outer container enforcement through the real judge path.

This script compiles small purpose-built C probes and runs them through the
current `autojudge.runner.run_test_case()` path. It is intended as an operator
smoke test after rebuilding run images.

Usage:
    uv run scripts/autojudge/probe_sandbox_limits.py
    uv run scripts/autojudge/probe_sandbox_limits.py --language gcc-c17
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import docker

sys.path.insert(0, str(Path(__file__).parents[2]))

from autojudge.config import settings
from autojudge.image_sync import _canonical_image_ref
from autojudge.pool import ContainerPool
from autojudge.runner import ProblemLimits, SubmissionSource, compile_submission, run_test_case
from shared.enumerations import Verdict
from shared.language_registry import LanguageConfig, default_language_registry, get_language

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
CYAN = "\033[36m"


def _c(text: str, color: str) -> str:
    return f"{color}{text}{RESET}"


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


@dataclass(frozen=True)
class Probe:
    name: str
    source_code: str
    expected_verdicts: tuple[Verdict, ...]
    expected_output: bytes = b""
    time_limit_ms: int = 1000
    memory_limit_kb: int = 262144
    pids_limit: int = 64
    output_limit_in_bytes: int | None = None


def _build_probes() -> list[Probe]:
    # Keep this small enough that the script remains a smoke test, but targeted
    # enough to verify isolate + outer Docker behavior.
    return [
        Probe(
            name="basic-ac",
            source_code=r"""
#include <stdio.h>
int main(void) { puts("OK"); return 0; }
""".strip(),
            expected_verdicts=(Verdict.AC,),
            expected_output=b"OK\n",
        ),
        Probe(
            name="network-none",
            source_code=r"""
#include <arpa/inet.h>
#include <netinet/in.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
int main(void) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) { puts("BLOCKED"); return 0; }
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(80);
    inet_pton(AF_INET, "1.1.1.1", &addr.sin_addr);
    int rc = connect(fd, (struct sockaddr *)&addr, sizeof(addr));
    close(fd);
    puts(rc == 0 ? "OPEN" : "BLOCKED");
    return rc == 0;
}
""".strip(),
            expected_verdicts=(Verdict.AC,),
            expected_output=b"BLOCKED\n",
        ),
        Probe(
            name="network-violation-yields-re",
            source_code=r"""
#include <arpa/inet.h>
#include <netinet/in.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
int main(void) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return 1;
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(80);
    inet_pton(AF_INET, "1.1.1.1", &addr.sin_addr);
    int rc = connect(fd, (struct sockaddr *)&addr, sizeof(addr));
    close(fd);
    return rc == 0 ? 0 : 1;
}
""".strip(),
            expected_verdicts=(Verdict.RE,),
        ),
        Probe(
            name="tle-enforcement",
            source_code=r"""
int main(void) {
    volatile unsigned long long x = 0;
    for (;;) { x++; }
}
""".strip(),
            expected_verdicts=(Verdict.TLE,),
            time_limit_ms=200,
        ),
        Probe(
            name="ole-enforcement",
            source_code=r"""
#include <stdio.h>
int main(void) {
    for (unsigned long i = 0; i < 100000UL; i++) putchar('A');
    return 0;
}
""".strip(),
            expected_verdicts=(Verdict.OLE,),
            output_limit_in_bytes=32 * 1024,
            time_limit_ms=5000,
        ),
        Probe(
            name="fsize-enforcement",
            source_code=r"""
#include <fcntl.h>
#include <unistd.h>
int main(void) {
    int fd = open("/sandbox/blob", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) return 2;
    char chunk[4096];
    for (unsigned long i = 0; i < sizeof(chunk); i++) chunk[i] = 'B';
    for (int i = 0; i < 1024; i++) {
        if (write(fd, chunk, sizeof(chunk)) < 0) return 3;
    }
    close(fd);
    return 0;
}
""".strip(),
            expected_verdicts=(Verdict.RE,),
            output_limit_in_bytes=32 * 1024,
            time_limit_ms=5000,
        ),
        Probe(
            name="tmp-write-allowed",
            source_code=r"""
#include <fcntl.h>
#include <stdio.h>
#include <unistd.h>
int main(void) {
    int fd = open("/tmp/noca-probe-outside", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd >= 0) {
        write(fd, "OK\n", 3);
        close(fd);
        puts("OPEN");
        return 0;
    }
    puts("BLOCKED");
    return 1;
}
""".strip(),
            expected_verdicts=(Verdict.AC,),
            expected_output=b"OPEN\n",
            time_limit_ms=5000,
        ),
        Probe(
            name="unmounted-path-write-blocked",
            source_code=r"""
#include <fcntl.h>
#include <stdio.h>
#include <unistd.h>
int main(void) {
    int fd = open("/root/noca-probe", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd >= 0) {
        close(fd);
        puts("OPEN");
        return 1;
    }
    puts("BLOCKED");
    return 0;
}
""".strip(),
            expected_verdicts=(Verdict.AC,),
            expected_output=b"BLOCKED\n",
            time_limit_ms=5000,
        ),
        Probe(
            name="unmounted-path-write-yields-re",
            source_code=r"""
#include <fcntl.h>
#include <unistd.h>
int main(void) {
    int fd = open("/root/noca-probe", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd >= 0) {
        close(fd);
        return 0;
    }
    return 1;
}
""".strip(),
            expected_verdicts=(Verdict.RE,),
            time_limit_ms=5000,
        ),
        Probe(
            name="system-dir-write-blocked",
            source_code=r"""
#include <fcntl.h>
#include <stdio.h>
#include <unistd.h>
int main(void) {
    int fd = open("/usr/noca-probe", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd >= 0) {
        close(fd);
        puts("OPEN");
        return 1;
    }
    puts("BLOCKED");
    return 0;
}
""".strip(),
            expected_verdicts=(Verdict.AC,),
            expected_output=b"BLOCKED\n",
            time_limit_ms=5000,
        ),
        Probe(
            name="system-dir-write-yields-re",
            source_code=r"""
#include <fcntl.h>
#include <unistd.h>
int main(void) {
    int fd = open("/usr/noca-probe", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd >= 0) {
        close(fd);
        return 0;
    }
    return 1;
}
""".strip(),
            expected_verdicts=(Verdict.RE,),
            time_limit_ms=5000,
        ),
        Probe(
            name="memory-enforcement",
            source_code=r"""
#include <stdlib.h>
#include <string.h>
int main(void) {
    const size_t chunk = 8 * 1024 * 1024;
    void **ptrs = calloc(64, sizeof(void *));
    if (!ptrs) return 1;
    for (int i = 0; i < 64; i++) {
        ptrs[i] = malloc(chunk);
        if (!ptrs[i]) return 1;
        memset(ptrs[i], 0xA5, chunk);
    }
    return 0;
}
""".strip(),
            expected_verdicts=(Verdict.MLE, Verdict.RE),
            memory_limit_kb=32 * 1024,
            time_limit_ms=5000,
        ),
        Probe(
            name="pids-enforcement",
            source_code=r"""
#include <errno.h>
#include <stdio.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
int main(void) {
    int children = 0;
    for (;;) {
        pid_t p = fork();
        if (p < 0) {
            return 1;
        }
        if (p == 0) {
            sleep(1);
            _exit(0);
        }
        children++;
        if (children > 128) break;
    }
    return 0;
}
""".strip(),
            expected_verdicts=(Verdict.RE,),
            pids_limit=8,
            time_limit_ms=5000,
        ),
    ]


async def run_probe(
    probe: Probe,
    language_id: str,
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor,
) -> bool:
    registry = default_language_registry()
    language = _apply_image_registry(get_language(registry, language_id))

    print(f"\n{BOLD}{CYAN}{'─' * 72}{RESET}")
    print(f"{BOLD}{CYAN}  Probe: {probe.name}{RESET}")
    print(f"{BOLD}{CYAN}{'─' * 72}{RESET}")
    print(f"  Run image:   {language.run_image}")
    print(f"  Compile img: {language.compile_image}")

    compile_result = await compile_submission(
        SubmissionSource(
            judgment_id=f"probe-{probe.name}-judgment",
            submission_id=f"probe-{probe.name}-submission",
            source_code=probe.source_code,
        ),
        language,
        docker_client,
        executor,
    )
    if not compile_result.success:
        print(_c("  ✗ compile failed", RED))
        if compile_result.compile_log:
            print(_c("  " + compile_result.compile_log.replace("\n", "\n  "), RED))
        return False

    pool = ContainerPool(
        language=language,
        docker_client=docker_client,
        executor=executor,
        target_size=1,
    )
    await pool.warm()
    container_id = await pool.acquire()

    try:
        started = time.monotonic()
        result = await run_test_case(
            container_id=container_id,
            language=language,
            limits=ProblemLimits(
                time_limit_ms=probe.time_limit_ms,
                memory_limit_kb=probe.memory_limit_kb,
                pids_limit=probe.pids_limit,
                output_limit_in_bytes=probe.output_limit_in_bytes,
            ),
            artifact_data=compile_result.artifact_data or b"",
            input_data=b"",
            expected_output=probe.expected_output,
            docker_client=docker_client,
            executor=executor,
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
    finally:
        await pool._kill_and_remove(container_id)
        await pool.shutdown()

    ok = result.verdict in probe.expected_verdicts
    color = GREEN if ok else RED
    expected = ", ".join(verdict.value for verdict in probe.expected_verdicts)
    print(
        _c(
            f"  {'✓' if ok else '✗'} verdict={result.verdict.value} expected={expected} "
            f"exit={result.exit_code} wall={result.wall_time_ms}ms mem={result.memory_kb}KB real={elapsed_ms}ms",
            color,
        )
    )
    if not ok and result.stdout_excerpt:
        print(_c(f"  stdout excerpt: {result.stdout_excerpt.decode(errors='replace')!r}", RED))
    if not ok and result.stderr_excerpt:
        print(_c(f"  stderr excerpt: {result.stderr_excerpt.decode(errors='replace')!r}", RED))
    return ok


async def main(language_id: str) -> int:
    probes = _build_probes()
    print(f"{BOLD}NOCA Sandbox Probe{RESET}")
    print(f"Language: {language_id}")
    print(f"Docker daemon: {settings.DOCKER_BASE_URL}")
    print(f"Probes: {len(probes)}")

    executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="docker-sdk")
    docker_client = docker.DockerClient(base_url=settings.DOCKER_BASE_URL, timeout=60)

    results: list[tuple[str, bool]] = []
    try:
        for probe in probes:
            results.append((probe.name, await run_probe(probe, language_id, docker_client, executor)))
    finally:
        docker_client.close()
        executor.shutdown(wait=False)

    print(f"\n{BOLD}{'─' * 72}{RESET}")
    print(f"{BOLD}Summary{RESET}")
    all_ok = True
    for name, ok in results:
        print(f"  {_c('✓ PASS', GREEN) if ok else _c('✗ FAIL', RED)}  {name}")
        all_ok = all_ok and ok
    return 0 if all_ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Probe isolate and outer container enforcement via the judge path.")
    parser.add_argument(
        "--language",
        default="gcc-c17",
        choices=["gcc-c17"],
        help="Language used for the probe programs",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.language)))
