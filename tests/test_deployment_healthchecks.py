#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Every NOCA service in the sample stack reports container health.

A service without a ``healthcheck`` shows up as a bare ``Up`` in ``docker ps``:
the container is running, but nothing observes whether the process inside it is
still doing its job, and ``depends_on: service_healthy`` cannot wait on it. The
HTTP runtimes probe their own ``/health`` endpoint over loopback; the workers
have no HTTP surface, so they probe the container-local heartbeat file they
refresh on an interval.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml.sample"

#: Compose services owned by NOCA, excluding the third-party infrastructure
#: images (postgres, valkey, caddy) that ship their own probes.
NOCA_SERVICES = (
    "landingpage",
    "web",
    "arena",
    "autojudge",
    "rating",
    "aiassistant",
    "mailer",
    "healthmonitor",
    "animator",
)

#: Worker services and the healthcheck module each one runs.
WORKER_PROBES = {
    "autojudge": "autojudge.healthcheck",
    "rating": "rating.healthcheck",
    "aiassistant": "aiassistant.healthcheck",
    "mailer": "mailer.healthcheck",
}


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    """Parse the sample compose stack once for the whole module."""
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("service", NOCA_SERVICES)
def test_service_declares_a_healthcheck(service: str, compose: dict[str, Any]) -> None:
    """Each NOCA service reports health rather than only liveness."""
    healthcheck = compose["services"][service].get("healthcheck")

    assert healthcheck is not None, f"{service} has no healthcheck"
    assert healthcheck["test"], f"{service} declares an empty healthcheck"
    assert healthcheck["start_period"], f"{service} has no healthcheck start_period"


@pytest.mark.parametrize("service", sorted(WORKER_PROBES))
def test_worker_healthcheck_runs_its_own_probe_module(service: str, compose: dict[str, Any]) -> None:
    """A worker has no HTTP surface, so it probes its heartbeat file in-process."""
    probe = " ".join(compose["services"][service]["healthcheck"]["test"])

    assert f"-m {WORKER_PROBES[service]}" in probe


def test_arena_healthcheck_probes_its_own_module(compose: dict[str, Any]) -> None:
    """Arena probes loopback through the module that reads NOCA_ARENA_PORT."""
    probe = " ".join(compose["services"]["arena"]["healthcheck"]["test"])

    assert "-m arena.healthcheck" in probe
