#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the suite-wide skip audit in ``tests/conftest.py``.

The audit exists because a skip is indistinguishable from a pass in a summary
line: the animator remote's Kotlin contract test spent a release cycle skipping
on a stale image pin while CI stayed green. These tests pin the policy itself,
need no toolchain, and therefore never skip — so the rule stays guarded on
exactly the machines where much of the suite does not run.
"""

from __future__ import annotations

import pytest

from tests.conftest import full_suite_is_required, skip_is_sanctioned


@pytest.mark.parametrize(
    ("environ", "required"),
    [
        pytest.param({}, False, id="developer-machine-may-skip"),
        pytest.param({"CI": "true"}, True, id="ci-requires-the-full-suite"),
        pytest.param({"NOCA_REQUIRE_FULL_SUITE": "1"}, True, id="explicit-opt-in-off-ci"),
        pytest.param({"CI": "true", "NOCA_REQUIRE_FULL_SUITE": ""}, False, id="ci-opt-out-empty"),
        pytest.param({"CI": "true", "NOCA_REQUIRE_FULL_SUITE": "0"}, False, id="ci-opt-out-zero"),
        pytest.param({"CI": "true", "NOCA_REQUIRE_FULL_SUITE": "no"}, False, id="ci-opt-out-no"),
    ],
)
def test_when_a_skip_must_fail_the_session(environ: dict[str, str], required: bool) -> None:
    """The override wins in both directions whenever it is present at all.

    A CI environment that genuinely cannot run part of the suite must be able to
    opt out deliberately rather than by accident, and a developer may want the
    guarantee without pretending to be CI.
    """
    assert full_suite_is_required(environ) is required


@pytest.mark.parametrize(
    ("nodeid", "markers", "sanctioned"),
    [
        # The four groups that legitimately cannot run without a credential or a
        # live service CI deliberately does not provide.
        pytest.param(
            "tests/autojudge/test_container_startup_real_docker.py::test_x",
            {"real_docker"},
            True,
            id="real-docker",
        ),
        pytest.param("tests/aiassistant/test_openai_e2e.py::test_x", {"real_openai"}, True, id="real-openai"),
        pytest.param(
            "tests/shared/test_email_reputation.py::test_live_good_email",
            {"real_ipqualityscore"},
            True,
            id="real-ipqualityscore",
        ),
        pytest.param("tests/browser/test_problem_editor_ui.py::test_x", set(), True, id="browser-by-path"),
        # A collection-level skip carries no markers at all, which is exactly why
        # the browser suite is matched by path rather than by marker.
        pytest.param("tests/browser/conftest.py", set(), True, id="browser-collection-skip"),
        # Everything else must run. These are the skips that previously hid a
        # broken environment behind a green summary line.
        pytest.param("tests/arena/test_problem_search_postgresql.py::test_x", set(), False, id="database-down"),
        pytest.param("tests/shared/test_contest_clock_js.py::test_x", set(), False, id="no-node"),
        pytest.param("tests/animator/test_remote_core_kotlin.py::test_x", set(), False, id="no-kotlin"),
        # A marker that is merely *about* real infrastructure is not a licence to
        # skip: CI provides PostgreSQL and Valkey, so those tests must run.
        pytest.param("tests/web/test_x.py::test_x", {"real_db"}, False, id="real-db-must-run"),
        pytest.param("tests/web/test_x.py::test_x", {"real_valkey"}, False, id="real-valkey-runs"),
        # A path that merely mentions the sanctioned one does not inherit it.
        pytest.param("tests/web/tests/browser/test_x.py::test_x", set(), False, id="not-a-prefix"),
    ],
)
def test_which_skips_are_sanctioned(nodeid: str, markers: set[str], sanctioned: bool) -> None:
    """Only an external credential or a live service CI lacks earns a skip."""
    assert skip_is_sanctioned(nodeid, markers) is sanctioned
