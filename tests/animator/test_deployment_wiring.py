#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Deployment-wiring tests for the animator container and sample stack.

These assertions cover the parts of the deployment that no unit test would
otherwise reach: configuration parity across ``config.py`` / ``.env.full`` /
``docs/CONFIG.md`` / compose, the image actually shipping every static directory
the app mounts, the schema-consumer boundary, and the proxy/publication wiring.
They are file-level checks precisely because the operational validation they
stand in for (building and running the image) is not always available.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from animator.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml.sample"
ENV_FULL = REPO_ROOT / ".env.full"
CONFIG_DOC = REPO_ROOT / "docs" / "CONFIG.md"
DOCKERFILE = REPO_ROOT / "containers" / "animator" / "Dockerfile"
ENTRYPOINT = REPO_ROOT / "containers" / "animator" / "entrypoint.sh"
CADDYFILE = REPO_ROOT / "containers" / "Caddyfile"
BUILD_SH = REPO_ROOT / "containers" / "build.sh"
BAKE_FILE = REPO_ROOT / "containers" / "docker-bake.hcl"
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-images.yml"
ANIMATOR_PYPROJECT = REPO_ROOT / "animator" / "pyproject.toml"
SHARED_PYPROJECT = REPO_ROOT / "shared" / "pyproject.toml"
MAIN_PY = REPO_ROOT / "animator" / "main.py"

#: Compose variables consumed by ``entrypoint.sh`` rather than by ``Settings``.
#: They are legitimately present in the service block but have no settings field.
ENTRYPOINT_OWNED_VARS = frozenset({"PUID", "PGID", "NOCA_WAIT_FOR_MIGRATIONS_TIMEOUT", "NOCA_ENVIRONMENT"})


def _settings_env_names() -> set[str]:
    """Return the environment variable name each animator setting binds to."""
    prefix = Settings.model_config.get("env_prefix", "")
    names: set[str] = set()
    for field_name, field in Settings.model_fields.items():
        alias = field.validation_alias
        names.add(str(alias) if isinstance(alias, str) else f"{prefix}{field_name}")
    return names


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    """Parsed sample compose file."""
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def animator_service(compose: dict[str, Any]) -> dict[str, Any]:
    """The compose ``animator`` service definition."""
    return compose["services"]["animator"]


# ---------------------------------------------------------------------------
# Configuration parity
# ---------------------------------------------------------------------------


def test_every_setting_is_documented() -> None:
    """Each animator setting appears in .env.full and docs/CONFIG.md.

    A setting that exists in code but in neither file is undiscoverable to an
    operator, which is how deployments end up running on accidental defaults.
    """
    env_text = ENV_FULL.read_text(encoding="utf-8")
    doc_text = CONFIG_DOC.read_text(encoding="utf-8")
    missing_env = sorted(n for n in _settings_env_names() if n not in env_text)
    missing_doc = sorted(n for n in _settings_env_names() if n not in doc_text)
    assert missing_env == [], f"absent from .env.full: {missing_env}"
    assert missing_doc == [], f"absent from docs/CONFIG.md: {missing_doc}"


def test_no_documented_animator_var_is_orphaned() -> None:
    """No NOCA_ANIMATOR_* name is documented without a backing setting."""
    known = _settings_env_names()
    pattern = re.compile(r"NOCA_ANIMATOR_[A-Z0-9_]+")
    documented = set(pattern.findall(ENV_FULL.read_text(encoding="utf-8")))
    documented |= set(pattern.findall(CONFIG_DOC.read_text(encoding="utf-8")))
    assert sorted(documented - known) == []


def test_compose_env_keys_are_real_settings(animator_service: dict[str, Any]) -> None:
    """Every compose variable is a settings field or a known entrypoint variable.

    A typo'd key is silently ignored by pydantic-settings, so the service would
    run on the default rather than the configured value.
    """
    keys = set(animator_service["environment"])
    unknown = sorted(keys - _settings_env_names() - ENTRYPOINT_OWNED_VARS)
    assert unknown == [], f"compose sets unknown variables: {unknown}"


def test_compose_declares_presence_and_proxy_settings(animator_service: dict[str, Any]) -> None:
    """Presence, proxy trust, and status-link settings are explicit, not implied."""
    keys = set(animator_service["environment"])
    assert {
        "NOCA_ANIMATOR_WORKER_ID",
        "NOCA_ANIMATOR_WORKER_PRESENCE_INTERVAL_SECONDS",
        "NOCA_ANIMATOR_WORKER_PRESENCE_TTL_SECONDS",
        "NOCA_FORWARDED_ALLOW_IPS",
        "NOCA_HEALTHMON_URL",
    } <= keys


# ---------------------------------------------------------------------------
# Healthcheck and port agreement
# ---------------------------------------------------------------------------


def test_healthcheck_port_matches_settings_and_expose(animator_service: dict[str, Any]) -> None:
    """Compose probe port, settings default port, and EXPOSE all agree.

    The probe interpolates ``NOCA_ANIMATOR_PORT`` so it follows a reconfigured
    port; its fallback is what must match the settings default and EXPOSE.
    """
    default_port = Settings.model_fields["PORT"].default
    probe = " ".join(animator_service["healthcheck"]["test"])
    assert "localhost:${NOCA_ANIMATOR_PORT:-" in probe
    assert f"localhost:${{NOCA_ANIMATOR_PORT:-{default_port}}}/health" in probe
    assert f"EXPOSE {default_port}" in DOCKERFILE.read_text(encoding="utf-8")


def test_healthcheck_uses_a_tool_the_image_installs(animator_service: dict[str, Any]) -> None:
    """The probe uses curl, which the shared app-base image installs.

    No animator-specific healthcheck helper is needed because of this.
    """
    probe = " ".join(animator_service["healthcheck"]["test"])
    assert "curl" in probe
    app_base = (REPO_ROOT / "containers" / "app-base" / "Dockerfile").read_text(encoding="utf-8")
    assert "curl" in app_base


# ---------------------------------------------------------------------------
# Image content: every mounted static directory ships
# ---------------------------------------------------------------------------


def test_dockerfile_ships_every_mounted_static_directory() -> None:
    """Each StaticFiles directory mounted by main.py is present in the image.

    A mount whose directory never made it into the image fails at startup, which
    only a real container run would otherwise reveal.
    """
    main_text = MAIN_PY.read_text(encoding="utf-8")
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    copied_roots = {line.split()[1].strip('"') for line in dockerfile.splitlines() if line.startswith("COPY ")}
    # Assets staged from the assets-base image land under shared/static/.
    staged = {m.group(1) for m in re.finditer(r"COPY --from=assets \S+ /app/(\S+)", dockerfile)}

    mounts = re.findall(r'directory=(_ANIMATOR_DIR|_SHARED_DIR)\s*/\s*"([^"]+)"([^)]*)\)', main_text)
    assert mounts, "no static mounts parsed from main.py"
    for base, first, rest in mounts:
        parts = [first, *re.findall(r'"([^"]+)"', rest)]
        root = "animator" if base == "_ANIMATOR_DIR" else "shared"
        relative = "/".join([root, *parts])
        directory = REPO_ROOT / relative
        assert directory.is_dir(), f"mounted directory does not exist: {relative}"
        shipped = root in copied_roots or any(relative.startswith(s) for s in staged)
        assert shipped, f"{relative} is mounted but never copied into the image"


# ---------------------------------------------------------------------------
# Schema-consumer boundary
# ---------------------------------------------------------------------------


def test_entrypoint_waits_for_migrations_and_never_runs_them() -> None:
    """The animator consumes the schema; it never stewards it.

    Stewardship belongs to the web and arena front doors. An animator that ran
    ``alembic upgrade head`` could drive the schema from a mismatched image
    during a rolling deploy.
    """
    # Compare executable lines only: the file's header comment legitimately
    # explains that run_migrations.py belongs to web/arena.
    commands = [
        line.strip()
        for line in ENTRYPOINT.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert any("scripts/wait_for_migrations.py" in line for line in commands)
    assert not any("run_migrations.py" in line for line in commands)


def test_dockerfile_copies_the_migration_script_it_runs() -> None:
    """The image ships exactly the migration script the entrypoint invokes."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "scripts/wait_for_migrations.py" in dockerfile
    assert "scripts/run_migrations.py" not in dockerfile


# ---------------------------------------------------------------------------
# Dependency boundary
# ---------------------------------------------------------------------------


def _direct_dependency_names(pyproject: Path) -> set[str]:
    """Return normalized distribution names declared by a pyproject file."""
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    names: set[str] = set()
    for spec in data["project"]["dependencies"]:
        name = re.split(r"[<>=!~\[@ ]", spec, maxsplit=1)[0].strip()
        if name:
            names.add(name.lower().replace("_", "-"))
    return names


#: Dependencies the animator legitimately owns because it is an HTTP
#: presentation server in its own right, which ``noca-shared`` (a library) does
#: not declare. Anything outside this set and shared's own list is unaccounted
#: for and must be justified before it is added here.
ANIMATOR_OWN_DEPENDENCIES = frozenset({"jinja2", "pydantic", "uvicorn"})


def test_animator_declares_no_unaccounted_dependency() -> None:
    """Every animator dependency is a workspace package, shared's, or its own.

    This is an ownership assertion rather than a denylist of today's Web-only
    packages: a Web-oriented dependency added to the animator slice tomorrow
    lands outside all three sets and fails here.
    """
    animator_deps = _direct_dependency_names(ANIMATOR_PYPROJECT)
    shared_deps = _direct_dependency_names(SHARED_PYPROJECT)
    workspace = {"noca-shared", "noca-animator"}
    unaccounted = sorted(animator_deps - shared_deps - workspace - ANIMATOR_OWN_DEPENDENCIES)
    assert unaccounted == [], f"unaccounted animator dependencies: {unaccounted}"


def _imported_top_level_modules(package_dir: Path) -> set[str]:
    """Return every top-level module imported by the Python sources in a tree.

    Uses ``ast`` rather than a text scan so prose in docstrings and comments can
    never be mistaken for an import statement.
    """
    modules: set[str] = set()
    for path in package_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
    return modules


def test_animator_declares_every_third_party_package_it_imports() -> None:
    """Each third-party top-level import under animator/ is a declared dependency."""
    local_or_future = {"animator", "shared", "__future__"}
    declared = _direct_dependency_names(ANIMATOR_PYPROJECT)
    # Import name differs from the distribution name for these packages.
    import_to_dist = {"pydantic_settings": "pydantic-settings", "dotenv": "python-dotenv"}

    third_party = {
        name
        for name in _imported_top_level_modules(REPO_ROOT / "animator")
        if name not in local_or_future and name not in sys.stdlib_module_names
    }
    missing = sorted(
        name for name in third_party if import_to_dist.get(name, name).lower().replace("_", "-") not in declared
    )
    assert missing == [], f"imported but not declared in animator/pyproject.toml: {missing}"


# ---------------------------------------------------------------------------
# Proxy and publication wiring
# ---------------------------------------------------------------------------


def test_caddy_routes_animator_without_breaking_sse() -> None:
    """Caddy proxies the animator and adds nothing that would buffer SSE.

    Caddy ignores ``flush_interval`` for ``text/event-stream`` and applies no
    stream timeout by default, so the feed streams correctly as long as nobody
    introduces response buffering or a finite ``stream_timeout``.
    """
    caddyfile = CADDYFILE.read_text(encoding="utf-8")
    default_port = Settings.model_fields["PORT"].default
    assert f"animator:{{$NOCA_ANIMATOR_PORT:{default_port}}}" in caddyfile
    assert ":83 {" in caddyfile
    assert "response_buffers" not in caddyfile
    assert "stream_timeout" not in caddyfile


def test_caddy_depends_on_animator(compose: dict[str, Any]) -> None:
    """Caddy starts after the animator so the :83 upstream resolves."""
    assert "animator" in compose["services"]["caddy"]["depends_on"]


def test_animator_is_in_every_build_and_publish_target_list() -> None:
    """The image is built by default and published on release.

    Without this the animator image exists in the tree but is never produced by
    the release pipeline.
    """
    assert "animator" in BUILD_SH.read_text(encoding="utf-8").split("APP_TARGETS=(")[1].split(")")[0]
    workflow = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"APP_TARGETS:.*\banimator\b", workflow)
    assert '"animator"' in BAKE_FILE.read_text(encoding="utf-8")


def test_deployment_carries_no_maratona_compatibility_asset() -> None:
    """The animator is a native runtime: no Maratona artifact participates."""
    forbidden = ("maratona", "animeitor", "webcast", "compatible-layer")
    for path in (DOCKERFILE, ENTRYPOINT):
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in text, f"{path.name} references {token}"
    service_text = yaml.safe_dump(
        yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))["services"]["animator"]
    ).lower()
    for token in forbidden:
        assert token not in service_text
