#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Deployment-contract tests for the standalone landing page."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
LANDINGPAGE_DIR = REPO_ROOT / "landingpage"
ENTRYPOINT = REPO_ROOT / "containers" / "landingpage" / "entrypoint.sh"
DOCKERFILE = REPO_ROOT / "containers" / "landingpage" / "Dockerfile"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml.sample"
BUILD_SCRIPT = REPO_ROOT / "containers" / "build.sh"
BAKE_FILE = REPO_ROOT / "containers" / "docker-bake.hcl"
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-images.yml"
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify_published_images.py"
ENV_FULL = REPO_ROOT / ".env.full"
CONFIG_DOC = REPO_ROOT / "docs" / "CONFIG.md"

URL_VARIABLES = (
    "NOCA_LANDINGPAGE_WEB_URL",
    "NOCA_LANDINGPAGE_ARENA_URL",
    "NOCA_LANDINGPAGE_ANIMATOR_URL",
    "NOCA_LANDINGPAGE_HEALTHMON_URL",
)
VERSION_VARIABLE = "NOCA_LANDINGPAGE_VERSION"
TEMPLATE_VARIABLES = (*URL_VARIABLES, VERSION_VARIABLE)


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    """Parse the sample Compose stack.

    Returns:
        The complete Compose document.
    """
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))


def _valid_environment() -> dict[str, str]:
    """Build a valid landing-page environment.

    Returns:
        A process environment containing every required public URL and the
        release tag shown in the page footer.
    """
    environment = os.environ.copy()
    for index, variable_name in enumerate(URL_VARIABLES):
        environment[variable_name] = f"https://module-{index}.example.test"
    # Deliberately not the repository's own version: these tests check the shape
    # the entrypoint accepts, never which release is current.
    environment[VERSION_VARIABLE] = "v0.0.0-test"
    return environment


def test_entrypoint_accepts_absolute_http_urls() -> None:
    """Valid HTTP(S) destinations let the configured command start."""
    result = subprocess.run(
        ["sh", str(ENTRYPOINT), "true"],
        check=False,
        capture_output=True,
        env=_valid_environment(),
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("invalid_value", ("", "arena.example.test", "javascript:alert(1)", "https://bad url"))
def test_entrypoint_rejects_missing_or_unsafe_urls(invalid_value: str) -> None:
    """Startup fails before Caddy sees a missing or unsafe destination."""
    environment = _valid_environment()
    environment[URL_VARIABLES[0]] = invalid_value

    result = subprocess.run(
        ["sh", str(ENTRYPOINT), "true"],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert result.returncode == 1
    assert URL_VARIABLES[0] in result.stderr


@pytest.mark.parametrize("invalid_value", ("", "v16 0", "v" * 33))
def test_entrypoint_rejects_missing_or_unusable_version(invalid_value: str) -> None:
    """A deployment cannot publish an unlabelled or malformed release tag."""
    environment = _valid_environment()
    environment[VERSION_VARIABLE] = invalid_value

    result = subprocess.run(
        ["sh", str(ENTRYPOINT), "true"],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert result.returncode == 1
    assert VERSION_VARIABLE in result.stderr


def test_template_presents_every_module_and_escapes_configured_values() -> None:
    """The page names every module without trusting configured markup."""
    template = (LANDINGPAGE_DIR / "index.html").read_text(encoding="utf-8")
    for variable_name in TEMPLATE_VARIABLES:
        assert f'{{{{env "{variable_name}" | html}}}}' in template
    # Product terminology, not workspace directory names: the `web` workspace is
    # the Contest product everywhere a reader can see it.
    for module_name in (
        "Contest",
        "Arena",
        "Animator",
        "Health Monitor",
        "AutoJudge",
        "Rating",
        "AI Assistant",
        "Shared services",
    ):
        assert module_name in template


def test_page_carries_no_inline_style_or_script() -> None:
    """Inline style and script would be blocked by the container's own CSP."""
    template = (LANDINGPAGE_DIR / "index.html").read_text(encoding="utf-8")
    assert "<style" not in template
    assert 'style="' not in template
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", template)


def test_page_assets_exist_and_are_same_origin() -> None:
    """Every asset the page requests is served from this container."""
    template = (LANDINGPAGE_DIR / "index.html").read_text(encoding="utf-8")
    references = set(re.findall(r'(?:href|src)="(/static/[^"]+)"', template))
    assert references, "the page should reference its own assets"

    # Fonts, the shared font stylesheet, and the product portraits are supplied by
    # the build rather than committed here; the Dockerfile test covers those.
    build_supplied = ("/static/webfonts/", "/static/vendor/", "/static/img/")
    for reference in sorted(references):
        if reference.startswith(build_supplied):
            continue
        assert (LANDINGPAGE_DIR / reference.lstrip("/")).is_file(), reference

    assert 'href="http' not in template.replace('href="https://github.com', "")


def test_caddy_contract_is_read_only_and_dependency_free() -> None:
    """Caddy exposes a static page and liveness route with defensive headers."""
    caddyfile = (LANDINGPAGE_DIR / "Caddyfile").read_text(encoding="utf-8")
    assert ":{$NOCA_LANDINGPAGE_PORT:8080}" in caddyfile
    assert "handle /health" in caddyfile
    assert 'respond `{"status":"ok"}` 200' in caddyfile
    assert "templates" in caddyfile
    assert "file_server" in caddyfile
    assert "reverse_proxy" not in caddyfile


def test_csp_denies_everything_it_does_not_name() -> None:
    """The page may load its own assets and reach nothing at all."""
    caddyfile = (LANDINGPAGE_DIR / "Caddyfile").read_text(encoding="utf-8")
    policy = re.search(r'header Content-Security-Policy "([^"]+)"', caddyfile)
    assert policy is not None
    directives = dict(
        (part.split(" ", 1) + [""])[:2] for part in (item.strip() for item in policy.group(1).split(";")) if part
    )
    assert directives["default-src"] == "'none'"
    for name in ("style-src", "script-src", "font-src"):
        assert directives[name] == "'self'"
    # A landing page that could call into the deployment it advertises would be a
    # data-plane dependency. It must not be able to.
    assert directives["connect-src"] == "'none'"
    for name in ("base-uri", "frame-ancestors", "form-action", "object-src"):
        assert directives[name] == "'none'"


def test_cache_matchers_do_not_overlap() -> None:
    """Cache lifetime is decided by disjoint matchers, not directive order.

    Every `header` directive sets the field, so a broad default plus narrower
    overrides silently loses: the broad one wins whatever the source order.
    """
    caddyfile = (LANDINGPAGE_DIR / "Caddyfile").read_text(encoding="utf-8")
    matched = re.findall(r"header @(\w+) Cache-Control", caddyfile)
    assert sorted(matched) == ["assets", "page", "webfonts"]
    assert not re.search(r"^\theader Cache-Control", caddyfile, re.MULTILINE)
    assert "@page path /\n" in caddyfile
    assert "@webfonts path /static/webfonts/*" in caddyfile


def test_container_is_small_pinned_and_unprivileged() -> None:
    """The module uses only a pinned official Caddy runtime as a non-root user."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "FROM caddy:2.11.4-alpine" in dockerfile
    assert "adduser -S -D -H -G caddy caddy" in dockerfile
    assert "USER caddy" in dockerfile
    assert "EXPOSE 8080" in dockerfile
    assert "node" not in dockerfile.lower()
    assert "python" not in dockerfile.lower()
    # The development stand-in for Caddy must never reach the runtime image.
    assert "serve_dev" not in dockerfile


def test_container_takes_shared_typography_from_the_assets_stage() -> None:
    """The page ships NOCA's typefaces without a CDN or a retyped hash."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "ASSETS_BASE_REF" in dockerfile
    assert "AS assets" in dockerfile
    assert "COPY --from=assets /app/shared/static/vendor/noca-fonts.css" in dockerfile
    for family in ("public-sans", "inter", "ibm-plex-mono"):
        assert f"/app/shared/static/webfonts/{family}-*.woff2" in dockerfile
    # Material Symbols and Font Awesome are 12 MB the page never references.
    for unused in ("mso.woff2", "msr.woff2", "mss.woff2", "fa-solid"):
        assert unused not in dockerfile

    # The build chain has to know it needs the assets stage, or the copy fails.
    build_script = BUILD_SCRIPT.read_text(encoding="utf-8")
    need_assets = build_script.split("NEED_ASSETS_BASE=1", 1)[0].rsplit("if [[", 1)[1]
    assert "landingpage" in need_assets
    assert 'inherits = ["_publish-common", "_assets-consumer"]' in BAKE_FILE.read_text(encoding="utf-8")


def test_module_portraits_come_from_the_modules_that_own_them() -> None:
    """Linked-module artwork is referenced, not duplicated into this module."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    for source in (
        "web/static/img/contest-256x256.png",
        "arena/static/img/logo-256x256.png",
        "animator/static/img/animator-256x256.png",
    ):
        assert source in dockerfile
        assert (REPO_ROOT / source).is_file()
    assert not (LANDINGPAGE_DIR / "static" / "img").exists()


def test_compose_wires_urls_port_and_healthcheck(compose: dict[str, Any]) -> None:
    """The sample deployment passes the complete runtime contract."""
    service = compose["services"]["landingpage"]
    environment = service["environment"]
    assert environment["NOCA_LANDINGPAGE_PORT"] == "${NOCA_LANDINGPAGE_PORT:-8080}"
    assert set(TEMPLATE_VARIABLES) <= set(environment)
    assert service["ports"] == ["84:${NOCA_LANDINGPAGE_PORT:-8080}"]
    assert "/health" in " ".join(service["healthcheck"]["test"])
    assert "depends_on" not in service


def test_configuration_is_documented() -> None:
    """Every landing-page variable is discoverable in both config references."""
    env_full = ENV_FULL.read_text(encoding="utf-8")
    config_doc = CONFIG_DOC.read_text(encoding="utf-8")
    for variable_name in ("NOCA_LANDINGPAGE_PORT", *TEMPLATE_VARIABLES):
        assert variable_name in env_full
        assert f"`{variable_name}`" in config_doc


def test_landingpage_is_in_every_build_and_publish_inventory() -> None:
    """Release automation cannot silently omit the standalone image."""
    build_targets = BUILD_SCRIPT.read_text(encoding="utf-8").split("APP_TARGETS=(", 1)[1].split(")", 1)[0]
    workflow = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    assert "landingpage" in build_targets
    assert 'target "landingpage"' in BAKE_FILE.read_text(encoding="utf-8")
    assert re.search(r"APP_TARGETS:.*\blandingpage\b", workflow)
    assert '"landingpage"' in VERIFY_SCRIPT.read_text(encoding="utf-8")
