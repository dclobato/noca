#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""End-to-end interactive judging against real Docker containers and isolate.

Exercises the whole per-test-case model on the real judge: one container pair is
reused across cases, each case's input is fed to the validator's stdin, and the
first case that does not end AC stops the iteration.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import AsyncMock

import docker
import docker.errors
import pytest
import pytest_asyncio

from autojudge.compiler import compile_submission
from autojudge.config import settings
from autojudge.custom_validator_submission import PreparedCustomValidator, run_custom_validator_submission
from autojudge.pool import PoolManager
from autojudge.types import ProblemLimits, SubmissionSource
from shared.enumerations import Verdict
from shared.language_registry import LanguageConfig, default_language_registry

pytestmark = pytest.mark.real_docker

# The validator reads its round from the test case, then plays it: the contestant
# must guess `secret` within `budget` queries.
VALIDATOR_SOURCE = """\
import sys

secret, budget = (int(value) for value in sys.stdin.readline().split())
for _ in range(budget):
    line = sys.stdin.readline()
    if not line:
        sys.exit(1)
    guess = int(line)
    if guess == secret:
        sys.exit(0)
    print("<" if guess > secret else ">", flush=True)
sys.exit(2)
"""

# A correct binary search: solves any round inside a sane query budget.
GOOD_CONTESTANT = """\
import sys

low, high = 1, 1000
while True:
    guess = (low + high) // 2
    print(guess, flush=True)
    hint = sys.stdin.readline().strip()
    if not hint:
        break
    if hint == "<":
        high = guess - 1
    else:
        low = guess + 1
"""

# Guesses linearly upward: fine when the secret is small, out of queries when it is not.
LAZY_CONTESTANT = """\
import sys

guess = 1
while True:
    print(guess, flush=True)
    hint = sys.stdin.readline().strip()
    if not hint:
        break
    guess += 1
"""

# Announces its deduced answer and exits at once, without waiting for the validator to
# acknowledge it — the shape of a real interactive solution, and of the exit race.
EAGER_CONTESTANT = """\
import sys

low, high = 1, 1000
while low < high:
    guess = low + (high - low) // 2 + 1
    print(guess, flush=True)
    hint = sys.stdin.readline().strip()
    if not hint:
        sys.exit(0)
    if hint == "<":
        high = guess - 1
    else:
        low = guess
print(f"!{low}", flush=True)
sys.exit(0)
"""

# Dies in the middle of the conversation, leaving the validator on a half-played round.
CRASHING_CONTESTANT = """\
import sys

print(500, flush=True)
sys.stdin.readline()
sys.exit(3)
"""

# The announce protocol: the round only ends when the contestant declares "!answer", and
# this validator then takes its time exiting. So the contestant, which exits the moment it
# has announced, is *always* the first process to end: the exit race becomes a certainty.
SLOW_EXIT_VALIDATOR = """\
import sys
import time

secret, budget = (int(value) for value in sys.stdin.readline().split())
for _ in range(budget):
    line = sys.stdin.readline().strip()
    if not line:
        sys.exit(1)
    if line.startswith("!"):
        time.sleep(1)
        sys.exit(0 if int(line[1:]) == secret else 1)
    guess = int(line)
    print("<" if guess > secret else ">=", flush=True)
sys.exit(2)
"""

# Reports the environment the judge promises it, then plays the round normally.
ENV_VALIDATOR = """\
import os
import sys

print(os.environ.get("USER_LANGUAGE"), os.environ.get("PROBLEM_TIME_LIMIT"), file=sys.stderr, flush=True)
secret, budget = (int(value) for value in sys.stdin.readline().split())
for _ in range(budget):
    line = sys.stdin.readline()
    if not line:
        sys.exit(1)
    if int(line) == secret:
        sys.exit(0)
    print("<" if int(line) > secret else ">", flush=True)
sys.exit(2)
"""


@pytest.fixture
def docker_client() -> docker.DockerClient:
    """Return a Docker client connected to the configured daemon."""
    if os.environ.get("NOCA_RUN_REAL_DOCKER_TESTS") != "1":
        pytest.skip("Set NOCA_RUN_REAL_DOCKER_TESTS=1 to run real Docker integration tests")

    client = docker.DockerClient(base_url=settings.DOCKER_BASE_URL, timeout=30)
    try:
        client.ping()
    except docker.errors.DockerException as exc:
        client.close()
        pytest.skip(f"Docker daemon is not available: {exc}")
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def python3(docker_client: docker.DockerClient) -> LanguageConfig:
    """The python3 language, pointed at whichever judge images exist locally.

    The shipped registry defaults are only defaults — a deployment stores its own
    image refs in the `languages` table — so resolve them against the local image
    store instead of hard-coding one publisher's prefix.
    """
    language = default_language_registry()["python3"]

    def _resolve(default_ref: str) -> str:
        repository, _, tag = default_ref.rpartition(":")
        suffix = repository.rpartition("/")[2]
        for candidate in (default_ref, *(f"{image.tags[0]}" for image in docker_client.images.list() if image.tags)):
            repo, _, candidate_tag = candidate.rpartition(":")
            if candidate_tag == tag and repo.rpartition("/")[2].endswith(suffix):
                try:
                    docker_client.images.get(candidate)
                except docker.errors.ImageNotFound:
                    continue
                return candidate
        pytest.skip(f"No local judge image matching '{default_ref}'; build the judge images first")

    return replace(
        language,
        compile_image=_resolve(language.compile_image),
        run_image=_resolve(language.run_image),
    )


@pytest_asyncio.fixture
async def pool(python3: LanguageConfig) -> AsyncGenerator[PoolManager]:
    """A live pool serving python3 run containers."""
    manager = PoolManager({python3.id: python3}, language_ids=[python3.id])
    try:
        yield manager
    finally:
        await manager.shutdown()


async def _judge(
    *,
    contestant_source: str,
    test_cases: list[tuple[int, bytes]],
    pool: PoolManager,
    python3: LanguageConfig,
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor,
    validator_source: str = VALIDATOR_SOURCE,
) -> tuple[Verdict | None, list[dict[str, object]]]:
    """Run one interactive judgment and return its verdict and persisted attempts."""
    validator_compile = await compile_submission(
        SubmissionSource("j", "validator", validator_source), python3, docker_client, executor
    )
    contestant_compile = await compile_submission(
        SubmissionSource("j", "contestant", contestant_source), python3, docker_client, executor
    )
    assert validator_compile.success and contestant_compile.artifact_data is not None

    db = AsyncMock()
    result, _ = await run_custom_validator_submission(
        domain="arena",
        judgment_id="judgment",
        problem_id="problem",
        contestant_language=python3,
        contestant_artifact=contestant_compile.artifact_data,
        limits=ProblemLimits(time_limit_ms=2000, memory_limit_kb=262144, pids_limit=64, output_limit_in_bytes=65536),
        test_cases=test_cases,
        db=db,
        pool_manager=pool,
        language_registry={python3.id: python3},
        docker_client=docker_client,
        executor=executor,
        prepared=PreparedCustomValidator(python3, validator_compile),
        user_language_id=python3.id,
    )
    assert result is not None
    attempts = [call.kwargs for call in db.insert_interactive_attempt.await_args_list]
    return result.classification.verdict, attempts


@pytest.mark.asyncio
async def test_every_case_passing_accepts_the_submission(
    pool: PoolManager, python3: LanguageConfig, docker_client: docker.DockerClient
) -> None:
    """A correct solution plays every round, so all cases run and the verdict is AC."""
    with ThreadPoolExecutor(max_workers=4) as executor:
        verdict, attempts = await _judge(
            contestant_source=GOOD_CONTESTANT,
            test_cases=[(1, b"42 50\n"), (2, b"7 50\n"), (3, b"999 50\n")],
            pool=pool,
            python3=python3,
            docker_client=docker_client,
            executor=executor,
        )

    assert verdict == Verdict.AC
    # Each case really ran, parametrized by its own input.
    assert [attempt["test_case_ordinal"] for attempt in attempts] == [1, 2, 3]


@pytest.mark.asyncio
async def test_iteration_stops_at_the_first_failing_case(
    pool: PoolManager, python3: LanguageConfig, docker_client: docker.DockerClient
) -> None:
    """The lazy solution wins the easy round, then burns its budget on the hard one."""
    with ThreadPoolExecutor(max_workers=4) as executor:
        verdict, attempts = await _judge(
            contestant_source=LAZY_CONTESTANT,
            test_cases=[(1, b"3 50\n"), (2, b"900 50\n"), (3, b"5 50\n")],
            pool=pool,
            python3=python3,
            docker_client=docker_client,
            executor=executor,
        )

    # Case 2 exhausts the query budget: the validator exits 2, which is TLE.
    assert verdict == Verdict.TLE
    # Case 3 never ran, and the retained attempt is the failing round.
    assert [attempt["test_case_ordinal"] for attempt in attempts] == [1, 2]


@pytest.mark.asyncio
async def test_a_contestant_exiting_on_its_final_answer_is_accepted(
    pool: PoolManager, python3: LanguageConfig, docker_client: docker.DockerClient
) -> None:
    """A contestant that exits first, cleanly, still gets the validator's verdict.

    The slow validator makes the contestant's exit reliably the first one the judge
    observes. Ranking that order above the validator's clean `AC` made this correct
    solution fail as `RE` whenever it happened to win the race.
    """
    with ThreadPoolExecutor(max_workers=4) as executor:
        verdict, attempts = await _judge(
            contestant_source=EAGER_CONTESTANT,
            test_cases=[(1, b"42 50\n"), (2, b"999 50\n")],
            pool=pool,
            python3=python3,
            docker_client=docker_client,
            executor=executor,
            validator_source=SLOW_EXIT_VALIDATOR,
        )

    assert attempts[-1]["result"].finished_first == "contestant"
    assert verdict == Verdict.AC


@pytest.mark.asyncio
async def test_a_contestant_crashing_mid_conversation_is_a_runtime_error(
    pool: PoolManager, python3: LanguageConfig, docker_client: docker.DockerClient
) -> None:
    """A real crash still outranks the validator's own exit code."""
    with ThreadPoolExecutor(max_workers=4) as executor:
        verdict, attempts = await _judge(
            contestant_source=CRASHING_CONTESTANT,
            test_cases=[(1, b"42 50\n")],
            pool=pool,
            python3=python3,
            docker_client=docker_client,
            executor=executor,
        )

    # The validator sees EOF and exits 1, which alone would read as WA.
    assert attempts[-1]["result"].validator_exit_code == 1
    assert verdict == Verdict.RE


@pytest.mark.asyncio
async def test_the_validator_process_receives_its_environment(
    pool: PoolManager, python3: LanguageConfig, docker_client: docker.DockerClient
) -> None:
    """The limit variables must survive isolate, which clears the child's environment."""
    with ThreadPoolExecutor(max_workers=4) as executor:
        verdict, attempts = await _judge(
            contestant_source=GOOD_CONTESTANT,
            test_cases=[(1, b"42 50\n")],
            pool=pool,
            python3=python3,
            docker_client=docker_client,
            executor=executor,
            validator_source=ENV_VALIDATOR,
        )

    assert verdict == Verdict.AC
    assert attempts[-1]["result"].validator_stderr_excerpt.strip() == b"python3 2000"


@pytest.mark.asyncio
async def test_the_transcript_holds_the_conversation_but_not_the_case_input(
    pool: PoolManager, python3: LanguageConfig, docker_client: docker.DockerClient
) -> None:
    """The relayed conversation is recorded; the case's own input is not."""
    with ThreadPoolExecutor(max_workers=4) as executor:
        verdict, attempts = await _judge(
            contestant_source=GOOD_CONTESTANT,
            test_cases=[(1, b"42 50\n")],
            pool=pool,
            python3=python3,
            docker_client=docker_client,
            executor=executor,
        )

    assert verdict == Verdict.AC
    transcript = attempts[-1]["result"].transcript
    lines = [(entry.direction, entry.line) for entry in transcript.lines]
    assert lines, "the conversation should have been recorded"
    # The contestant opens by guessing, and the validator answers with hints.
    assert lines[0][0] == "user"
    assert {direction for direction, _ in lines} == {"user", "validator"}
    assert all(hint in {"<", ">"} for direction, hint in lines if direction == "validator")
    # "42 50" is the problem's data, not something either side said.
    assert not any(line.strip() == "42 50" for _, line in lines)
