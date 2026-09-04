#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Structural checks on the layered ``.env.<layer>.full`` templates.

``.env.full`` used to be one file holding every variable, which made "does this
service need this setting?" unanswerable and let the sample stack pass secrets to
containers that never read them. The split into layers only stays true if three
things are enforced mechanically, because none of them is visible in a diff:

* every field of a module's ``Settings`` is defined by exactly one layer in that
  module's stack -- otherwise a new setting lands in ``docs/CONFIG.md`` and in no
  template, and the operator learns about it from a startup traceback;
* no variable is defined twice -- two copies of ``NOCA_DB_PASSWORD`` drift, and
  the one that wins depends on ``env_file`` order rather than on intent;
* ``docker-compose.yml.sample`` lists exactly the manifest's stack, in order, and
  never interpolates a variable a layer owns -- Compose resolves ``${...}`` from
  the project-root ``.env`` and the shell, never from an ``env_file``, so such a
  reference silently yields the interpolation default while the layer's real
  value goes unread. That failure is invisible: the stack starts, and the proxy
  simply talks to the wrong port.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "env_layers.toml"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml.sample"
CONFIG_DOC = REPO_ROOT / "docs" / "CONFIG.md"

ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
INTERPOLATION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)")

MANIFEST_DATA = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
LAYERS: dict[str, str] = MANIFEST_DATA["layers"]
STANDALONE: dict[str, str] = MANIFEST_DATA["standalone"]
STACKS: dict[str, list[str]] = MANIFEST_DATA["stacks"]

SETTINGS_MODULES = (
    "web",
    "arena",
    "autojudge",
    "rating",
    "aiassistant",
    "mailer",
    "healthmonitor",
    "animator",
)


def _layer_path(layer: str) -> Path:
    return REPO_ROOT / f".env.{layer}.full"


def _variables(path: Path) -> list[str]:
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = ASSIGNMENT.match(line)
        if match:
            names.append(match.group(1))
    return names


def _settings_env_names(module: str) -> set[str]:
    """Env var names one module's ``Settings`` class actually reads."""
    settings_cls = __import__(f"{module}.config", fromlist=["Settings"]).Settings
    prefix = settings_cls.model_config.get("env_prefix", "")
    names = set()
    for field_name, field in settings_cls.model_fields.items():
        alias = field.validation_alias
        names.add(alias.upper() if isinstance(alias, str) else (prefix + field_name).upper())
    return names


def test_every_template_is_in_the_manifest() -> None:
    """No stray or missing ``.env.*.full`` file."""
    on_disk = {p.name.removeprefix(".env.").removesuffix(".full") for p in REPO_ROOT.glob(".env.*.full")}
    declared = set(LAYERS) | set(STANDALONE)
    assert on_disk == declared, f"on disk only: {on_disk - declared}; declared only: {declared - on_disk}"


def test_no_variable_is_defined_by_two_templates() -> None:
    """A shared value has exactly one home, so its copies cannot drift apart."""
    owners: dict[str, list[str]] = {}
    for layer in sorted(set(LAYERS) | set(STANDALONE)):
        for name in _variables(_layer_path(layer)):
            owners.setdefault(name, []).append(layer)
    duplicated = {name: files for name, files in owners.items() if len(files) > 1}
    assert duplicated == {}, f"defined more than once: {duplicated}"


@pytest.mark.parametrize("module", SETTINGS_MODULES)
def test_module_stack_covers_its_settings(module: str) -> None:
    """Each setting a module reads is supplied by a layer that module loads."""
    supplied: set[str] = set()
    for layer in STACKS[module]:
        supplied.update(_variables(_layer_path(layer)))
    missing = sorted(_settings_env_names(module) - supplied)
    assert missing == [], f"{module} reads these but no layer in its stack defines them: {missing}"


@pytest.mark.parametrize("module", SETTINGS_MODULES)
def test_module_layer_holds_only_that_module_s_settings(module: str) -> None:
    """A per-module template never accumulates another module's variables."""
    own = _settings_env_names(module)
    strays = sorted(name for name in _variables(_layer_path(module)) if name not in own)
    assert strays == [], f".env.{module}.full defines variables {module} does not read: {strays}"


def test_compose_sample_wires_the_declared_stacks() -> None:
    """The sample stack's ``env_file`` lists match the manifest exactly, in order."""
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    for service, layers in STACKS.items():
        expected = [f".env.{layer}.full" for layer in layers]
        actual = compose["services"][service].get("env_file")
        assert actual == expected, f"{service}: expected {expected}, found {actual}"


def test_every_variable_is_documented() -> None:
    """``docs/CONFIG.md`` remains the reference every template points at.

    ``devtools`` is excluded: it holds the Gitea token the backlog generator uses
    and the Playwright login, which are repository tooling rather than deployment
    configuration, and CONFIG.md deliberately says a deployment never needs them.
    """
    documented = CONFIG_DOC.read_text(encoding="utf-8")
    undocumented = []
    for layer in sorted((set(LAYERS) | set(STANDALONE)) - {"devtools"}):
        for name in _variables(_layer_path(layer)):
            if f"`{name}`" not in documented:
                undocumented.append(f"{layer}:{name}")
    assert undocumented == [], f"absent from docs/CONFIG.md: {undocumented}"


def test_no_compose_interpolation_reads_an_env_file_layer() -> None:
    """``${...}`` may only name variables the project-root ``.env`` supplies.

    Compose resolves interpolation from the shell and the project-root ``.env``.
    An ``env_file:`` entry feeds the container and nothing else, so
    ``${NOCA_WEB_PORT:-8000}`` beside ``env_file: .env.web.full`` does not read
    that file -- it yields 8000 whatever the file says, and because
    ``environment:`` outranks every ``env_file`` it then overrides the real value
    on the way in. Only the standalone templates, which belong in the root
    ``.env``, are legitimate interpolation sources.
    """
    owner: dict[str, str] = {}
    for layer in sorted(set(LAYERS) | set(STANDALONE)):
        for name in _variables(_layer_path(layer)):
            owner[name] = layer

    compose = COMPOSE_FILE.read_text(encoding="utf-8")
    offenders = sorted(
        f"${{{name}}} is owned by .env.{owner[name]}.full"
        for name in set(INTERPOLATION.findall(compose))
        if owner.get(name) in LAYERS
    )
    assert offenders == [], f"interpolation cannot read an env_file layer: {offenders}"
