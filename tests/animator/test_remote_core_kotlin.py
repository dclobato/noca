#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Run the Android remote's command-client contract tests under Kotlin.

The Android operator remote in ``clients/animator-remote`` re-implements the
safety rules that ``animator/static/js/control.js`` encodes — above all the
ambiguous-outcome lock that stops a ceremony being advanced twice in front of an
audience. Those rules live in a pure-Kotlin core with an injected HTTP port, so
they can be exercised on a plain JVM with no Android SDK, no emulator, and no
network.

This wrapper is the exact counterpart of ``test_ceremony_js.py``, which runs the
Node contract tests for the browser panel: it shells out to the real toolchain and
**skips cleanly** when that toolchain is absent, so a Python-only checkout still
has a green suite while a developer machine with Docker gets real coverage.

That leniency is deliberately switched off where the coverage is load-bearing.
This test spent a release cycle skipping on a stale image pin and said nothing
about it, which is the failure mode a skip always has: it is indistinguishable
from a pass. Under CI — or wherever ``NOCA_KOTLIN_REQUIRED`` is set — every
reason this test would have skipped becomes a failure instead, so the coverage
cannot be lost again without someone being told.

The suite-wide skip audit in ``tests/conftest.py`` is the backstop for the same
rule and does not sanction this test either, so a skip here fails CI twice over.
This local check is kept because it fails at the point of the skip with a reason
naming the toolchain, which is what someone reading a red build needs first.

The complementary ``test_remote_client_contract.py`` needs no toolchain and never
skips, so wire-contract drift is caught even where this test is skipped.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

_RUNNER = Path(__file__).resolve().parents[2] / "clients" / "animator-remote" / "tools" / "run-core-tests.sh"

# Compiling the core plus its tests inside the container takes well under a
# minute on a warm image, but the first run may also fetch three runtime jars.
_TIMEOUT_SECONDS = 300

# The runner exits with this when its toolchain is unavailable, which is a skip
# rather than a failure -- unless this environment requires the coverage.
_TOOLCHAIN_MISSING = 127

# `CI` is the default signal -- Gitea Actions sets it, as does every other runner
# worth naming. `NOCA_KOTLIN_REQUIRED` states the demand explicitly and wins in
# both directions whenever it is present at all, including when set to an empty
# value: an environment that reports `CI` but genuinely cannot run Docker needs a
# way to opt out, and one that is not CI at all may still want the guarantee.
_DISABLED_VALUES = {"", "0", "false", "no"}


def _coverage_is_required(environ: Mapping[str, str]) -> bool:
    """Return whether an absent toolchain must fail rather than skip."""
    override = environ.get("NOCA_KOTLIN_REQUIRED")
    if override is not None:
        return override.strip().lower() not in _DISABLED_VALUES
    return bool(environ.get("CI"))


_REQUIRED = _coverage_is_required(os.environ)


def _unavailable(reason: str) -> None:
    """Skip for this reason, or fail if this environment requires the coverage."""
    if _REQUIRED:
        pytest.fail(
            f"the Kotlin contract test is required here but did not run: {reason}. "
            "Set NOCA_KOTLIN_REQUIRED to an empty value to allow skipping."
        )
    pytest.skip(reason)


def test_animator_remote_core_contract() -> None:
    """The operator command client's ambiguous-outcome lock and wire shapes hold."""
    if not _RUNNER.is_file():
        _unavailable(f"{_RUNNER.name} is not present")
    if shutil.which("docker") is None:
        _unavailable("Docker is not available")

    try:
        result = subprocess.run(
            ["bash", str(_RUNNER)],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:  # pragma: no cover - environment dependent
        pytest.fail(f"the Kotlin contract test did not finish within {_TIMEOUT_SECONDS}s")

    if result.returncode == _TOOLCHAIN_MISSING:
        _unavailable(f"Kotlin toolchain unavailable: {result.stderr.strip()}")

    assert result.returncode == 0, f"the Kotlin core contract test failed:\n{result.stdout}\n{result.stderr}"


@pytest.mark.parametrize(
    ("environ", "required"),
    [
        pytest.param({}, False, id="developer-machine-skips"),
        pytest.param({"CI": "true"}, True, id="ci-requires"),
        pytest.param({"NOCA_KOTLIN_REQUIRED": "1"}, True, id="explicit-opt-in-off-ci"),
        pytest.param({"CI": "true", "NOCA_KOTLIN_REQUIRED": ""}, False, id="ci-opt-out-empty"),
        pytest.param({"CI": "true", "NOCA_KOTLIN_REQUIRED": "0"}, False, id="ci-opt-out-zero"),
    ],
)
def test_ci_cannot_silently_lose_the_kotlin_coverage(environ: dict[str, str], required: bool) -> None:
    """A skip is indistinguishable from a pass, so CI must not be allowed one.

    This test needs no toolchain and therefore never skips itself -- which is the
    point: it still guards the policy on exactly the machines where the Kotlin
    test above is skipped. The override wins in both directions whenever it is
    present at all, so a CI environment that genuinely cannot run Docker can opt
    out deliberately rather than by accident.
    """
    assert _coverage_is_required(environ) is required
