#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Cross-module parity for the configurable HTTP bind address and port.

Each HTTP runtime binds ``NOCA_<MODULE>_HOST`` / ``NOCA_<MODULE>_PORT``, and in
the sample stack that same port variable also drives the Caddy upstream and the
container healthcheck. Those are four separate files that no unit test would
otherwise compare, so a port default can drift in one of them and only surface
as a dead upstream at deploy time. These file-level checks pin them together.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from animator.config import Settings as AnimatorSettings
from arena.config import Settings as ArenaSettings
from healthmonitor.config import Settings as HealthmonSettings
from web.config import Settings as WebSettings

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml.sample"
CADDYFILE = REPO_ROOT / "containers" / "Caddyfile"
ENV_FULL = REPO_ROOT / ".env.full"
CONFIG_DOC = REPO_ROOT / "docs" / "CONFIG.md"
ARENA_DOCKERFILE = REPO_ROOT / "containers" / "arena" / "Dockerfile"


@dataclass(frozen=True)
class ServiceBinding:
    """One HTTP runtime's bind configuration across code and deployment files."""

    service: str
    host_var: str
    port_var: str
    settings: type
    dockerfile: Path
    #: Whether the compose healthcheck probes the service over loopback.
    loopback_probe: bool


BINDINGS = (
    ServiceBinding(
        service="web",
        host_var="NOCA_WEB_HOST",
        port_var="NOCA_WEB_PORT",
        settings=WebSettings,
        dockerfile=REPO_ROOT / "containers" / "webapp" / "Dockerfile",
        loopback_probe=False,
    ),
    ServiceBinding(
        service="arena",
        host_var="NOCA_ARENA_HOST",
        port_var="NOCA_ARENA_PORT",
        settings=ArenaSettings,
        dockerfile=REPO_ROOT / "containers" / "arena" / "Dockerfile",
        loopback_probe=False,
    ),
    ServiceBinding(
        service="healthmonitor",
        host_var="NOCA_HEALTHMON_HOST",
        port_var="NOCA_HEALTHMON_PORT",
        settings=HealthmonSettings,
        dockerfile=REPO_ROOT / "containers" / "healthmonitor" / "Dockerfile",
        loopback_probe=True,
    ),
    ServiceBinding(
        service="animator",
        host_var="NOCA_ANIMATOR_HOST",
        port_var="NOCA_ANIMATOR_PORT",
        settings=AnimatorSettings,
        dockerfile=REPO_ROOT / "containers" / "animator" / "Dockerfile",
        loopback_probe=True,
    ),
)

_IDS = [binding.service for binding in BINDINGS]


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    """Parse the sample compose stack once for the whole module."""
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def caddyfile() -> str:
    """Read the sample Caddyfile once for the whole module."""
    return CADDYFILE.read_text(encoding="utf-8")


@pytest.mark.parametrize("binding", BINDINGS, ids=_IDS)
def test_settings_expose_host_and_port(binding: ServiceBinding) -> None:
    """Every HTTP runtime binds an explicitly aliased host and port."""
    fields = binding.settings.model_fields
    assert str(fields["HOST"].validation_alias) == binding.host_var
    assert str(fields["PORT"].validation_alias) == binding.port_var
    assert fields["HOST"].default == "0.0.0.0"


@pytest.mark.parametrize("binding", BINDINGS, ids=_IDS)
def test_compose_declares_host_and_port_with_settings_defaults(
    binding: ServiceBinding, compose: dict[str, Any]
) -> None:
    """Compose passes both variables through, defaulting to the settings values."""
    environment = compose["services"][binding.service]["environment"]
    default_port = binding.settings.model_fields["PORT"].default

    assert environment[binding.host_var] == f"${{{binding.host_var}:-0.0.0.0}}"
    assert environment[binding.port_var] == f"${{{binding.port_var}:-{default_port}}}"


@pytest.mark.parametrize("binding", BINDINGS, ids=_IDS)
def test_caddy_upstream_follows_the_same_port_variable(binding: ServiceBinding, caddyfile: str) -> None:
    """The proxy resolves each upstream port from the variable the service binds."""
    default_port = binding.settings.model_fields["PORT"].default
    assert f"{binding.service}:{{${binding.port_var}:{default_port}}}" in caddyfile


@pytest.mark.parametrize("binding", BINDINGS, ids=_IDS)
def test_caddy_receives_the_port_variables_it_substitutes(binding: ServiceBinding, compose: dict[str, Any]) -> None:
    """Caddy reads {$VAR:default} from its own environment, so compose must pass it.

    Without this the proxy would silently fall back to the default port while the
    service listens elsewhere.
    """
    default_port = binding.settings.model_fields["PORT"].default
    caddy_environment = compose["services"]["caddy"]["environment"]

    assert caddy_environment[binding.port_var] == f"${{{binding.port_var}:-{default_port}}}"


@pytest.mark.parametrize("binding", BINDINGS, ids=_IDS)
def test_expose_matches_the_default_port(binding: ServiceBinding) -> None:
    """EXPOSE is documentary, but must still name the default the image listens on."""
    default_port = binding.settings.model_fields["PORT"].default
    assert f"EXPOSE {default_port}" in binding.dockerfile.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "binding", [b for b in BINDINGS if b.loopback_probe], ids=[b.service for b in BINDINGS if b.loopback_probe]
)
def test_loopback_healthcheck_follows_the_port_variable(binding: ServiceBinding, compose: dict[str, Any]) -> None:
    """A reconfigured port must not leave the container probing the old one."""
    default_port = binding.settings.model_fields["PORT"].default
    probe = " ".join(compose["services"][binding.service]["healthcheck"]["test"])

    assert f"localhost:${{{binding.port_var}:-{default_port}}}/health" in probe


@pytest.mark.parametrize("binding", BINDINGS, ids=_IDS)
def test_variables_are_documented(binding: ServiceBinding) -> None:
    """Both variables appear in .env.full and docs/CONFIG.md."""
    env_full = ENV_FULL.read_text(encoding="utf-8")
    config_doc = CONFIG_DOC.read_text(encoding="utf-8")
    default_port = binding.settings.model_fields["PORT"].default

    assert f"{binding.host_var}=0.0.0.0" in env_full
    assert f"{binding.port_var}={default_port}" in env_full
    assert f"`{binding.host_var}`" in config_doc
    assert f"`{binding.port_var}`" in config_doc


def test_arena_image_asserts_shared_medal_renderer_and_artwork() -> None:
    """The Arena image build fails if any leaderboard medal dependency is absent."""
    dockerfile = ARENA_DOCKERFILE.read_text(encoding="utf-8")
    required_paths = (
        "/app/shared/services/balloon_assets.py",
        "/app/shared/services/assets/gold.svg",
        "/app/shared/services/assets/silver.svg",
        "/app/shared/services/assets/bronze.svg",
    )

    assert "COPY shared /app/shared" in dockerfile
    assert all(f"test -f {path}" in dockerfile for path in required_paths)
