#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Pin the Android operator remote to the animator's real wire contract.

The Android client in ``clients/animator-remote`` talks to the reveal control API
over hand-written models: the animator serves no OpenAPI document
(``openapi_url=None``), so nothing can be generated from the running app.

That leaves one way for the two sides to drift silently — a field added, renamed,
or retyped on the server that the remote never learns about — and this module
closes it. Unlike the Kotlin contract test (``test_remote_core_kotlin.py``), it
needs **no Kotlin toolchain and never skips**: it runs the server's own Pydantic
models against the JSON fixtures the Kotlin tests consume, and greps the Kotlin
models for the field names those models serialize. A server-side change that would
break an operator's phone mid-ceremony therefore fails the ordinary Python suite,
on any machine.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from animator.models.command_receipt import IDEMPOTENCY_KEY_PATTERN
from animator.models.control import JumpTeamRequest, StartRevealRequest
from animator.models.responses import ContestMetaResponse, ProblemMeta, RevealProjectionResponse, SiteMeta
from animator.models.reveal_session import MedalCutoffs, NextRevealCell, ProblemRevealView, TeamRevealView
from animator.routes.control import _ERROR_MAP

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLIENT_ROOT = _REPO_ROOT / "clients" / "animator-remote"
_KOTLIN_CORE = _CLIENT_ROOT / "app" / "src" / "main" / "kotlin" / "org" / "noca" / "animator" / "remote" / "core"
_FIXTURES = _CLIENT_ROOT / "app" / "src" / "test" / "resources" / "fixtures"

try:
    from animator.models.controller_lease import ControllerLeaseResponse
except ImportError:
    ControllerLeaseResponse = None

_EXPECTED_CONTROLLER_ID_HEADER = "X-Animator-Controller-Id"
_EXPECTED_CONTROLLER_ID_PATTERN = r"^[A-Za-z0-9_-]{8,128}$"


def _kotlin(name: str) -> str:
    """Return the text of one Kotlin core source file."""
    path = _KOTLIN_CORE / name
    assert path.is_file(), f"missing Kotlin source {path}"
    return path.read_text(encoding="utf-8")


def _fixture(name: str) -> dict[str, Any]:
    """Return one parsed JSON fixture shared with the Kotlin tests."""
    path = _FIXTURES / name
    assert path.is_file(), f"missing fixture {path}"
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def _serialized_keys(model: type[BaseModel]) -> set[str]:
    """Return every key ``model`` puts on the wire, including computed fields."""
    keys = {field.alias or name for name, field in model.model_fields.items()}
    return keys | set(model.model_computed_fields)


def _serial_names(source: str) -> set[str]:
    """Return every ``@SerialName("...")`` literal in a Kotlin source."""
    return set(re.findall(r'@SerialName\("([^"]+)"\)', source))


def _property_names(source: str) -> set[str]:
    """Return every ``val <name>:`` property identifier in a Kotlin source."""
    return set(re.findall(r"\bval\s+([A-Za-z_][A-Za-z0-9_]*)\s*:", source))


def _camel(snake: str) -> str:
    """Convert ``a_b_c`` to ``aBC``-style Kotlin property naming."""
    head, *rest = snake.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def _assert_kotlin_covers(model: type[BaseModel], source: str, *, label: str) -> None:
    """Assert the Kotlin models name every key ``model`` serializes."""
    serial_names = _serial_names(source)
    properties = _property_names(source)
    missing = sorted(
        key for key in _serialized_keys(model) if key not in serial_names and _camel(key) not in properties
    )
    assert not missing, (
        f"{label} serializes {missing} but the Kotlin client models name neither the "
        f"@SerialName literal nor a matching property. Update "
        f"clients/animator-remote/.../core/ApiModels.kt and regenerate the fixtures."
    )


# ---------------------------------------------------------------------------
# the fixtures are valid instances of the server's own models
# ---------------------------------------------------------------------------


def test_projection_fixture_validates_against_the_server_model() -> None:
    """The shared projection fixture is a real ``RevealProjectionResponse``."""
    RevealProjectionResponse.model_validate(_fixture("projection.json"))


def test_meta_fixture_validates_against_the_server_model() -> None:
    """The shared meta fixture is a real ``ContestMetaResponse``."""
    ContestMetaResponse.model_validate(_fixture("meta.json"))


def test_lease_fixture_matches_the_pending_server_contract() -> None:
    """Pin timings now and validate with the server model as soon as it lands."""
    payload = _fixture("controller-lease.json")
    assert payload["lease_ttl_seconds"] == 45
    assert payload["heartbeat_interval_seconds"] == 10
    assert isinstance(payload["status"], str)
    if ControllerLeaseResponse is not None:
        ControllerLeaseResponse.model_validate(payload)
        assert set(payload) == _serialized_keys(ControllerLeaseResponse)


@pytest.mark.parametrize(
    ("fixture_name", "model"),
    [("projection.json", RevealProjectionResponse), ("meta.json", ContestMetaResponse)],
)
def test_fixture_covers_every_serialized_field(fixture_name: str, model: type[BaseModel]) -> None:
    """A fixture must exercise every field, or it cannot detect a new one.

    An incomplete fixture is the failure mode that matters here: the Kotlin test
    would keep passing while quietly never seeing the field the server added.
    """
    payload = _fixture(fixture_name)
    assert set(payload) == _serialized_keys(model), (
        f"{fixture_name} no longer matches {model.__name__}; regenerate the fixture so the "
        f"Kotlin client is exercised against every field."
    )


def test_projection_fixture_covers_nested_models() -> None:
    """The nested team, cell, medal, and next-cell shapes are covered too."""
    payload = _fixture("projection.json")

    teams = payload["teams"]
    assert teams, "the fixture must carry at least one team"
    for team in teams:
        assert set(team) == _serialized_keys(TeamRevealView)

    problems = [cell for team in teams for cell in team["problems"].values()]
    assert problems, "the fixture must carry at least one problem cell"
    for cell in problems:
        assert set(cell) == _serialized_keys(ProblemRevealView)

    assert set(payload["medal_cutoffs"]) == _serialized_keys(MedalCutoffs)
    assert set(payload["next_cell"]) == _serialized_keys(NextRevealCell)

    # The remote renders a pending cell from this computed field, so it must be
    # serialized rather than merely derivable.
    assert any(cell["pending_frozen"] for cell in problems), (
        "the fixture must include a pending cell so the remote's pending rendering is covered"
    )


def test_meta_fixture_covers_nested_models() -> None:
    """The problem and site shapes the scope picker reads are covered."""
    payload = _fixture("meta.json")
    assert payload["problems"], "the fixture must carry at least one problem"
    assert payload["sites"], "the fixture must carry at least one site"
    for problem in payload["problems"]:
        assert set(problem) == _serialized_keys(ProblemMeta)
    for site in payload["sites"]:
        assert set(site) == _serialized_keys(SiteMeta)


# ---------------------------------------------------------------------------
# the Kotlin client names every field the server sends
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    [
        RevealProjectionResponse,
        TeamRevealView,
        ProblemRevealView,
        MedalCutoffs,
        NextRevealCell,
        ContestMetaResponse,
        ProblemMeta,
        SiteMeta,
    ],
)
def test_kotlin_models_name_every_response_field(model: type[BaseModel]) -> None:
    """Every response field the animator sends is named by the Kotlin models."""
    _assert_kotlin_covers(model, _kotlin("ApiModels.kt"), label=model.__name__)


@pytest.mark.parametrize("model", [StartRevealRequest, JumpTeamRequest])
def test_kotlin_request_bodies_name_every_request_field(model: type[BaseModel]) -> None:
    """The request bodies the remote builds carry exactly the server's fields.

    The control API's request models are ``extra="forbid"``, so a field the remote
    invents is a ``422`` and a field it omits changes the command's meaning.
    """
    _assert_kotlin_covers(model, _kotlin("CommandClient.kt"), label=model.__name__)


# ---------------------------------------------------------------------------
# the constants both sides must agree on, exactly
# ---------------------------------------------------------------------------


def test_kotlin_idempotency_pattern_matches_the_server() -> None:
    """The client's key generator is validated against the server's own regex.

    A drifted pattern would surface as a puzzling ``422`` on the operator's phone
    in the middle of a ceremony.
    """
    source = _kotlin("CommandClient.kt")
    match = re.search(r'IDEMPOTENCY_KEY_PATTERN\s*:\s*Regex\s*=\s*Regex\("([^"]+)"\)', source)
    assert match, "could not find IDEMPOTENCY_KEY_PATTERN in the Kotlin client"
    assert match.group(1) == IDEMPOTENCY_KEY_PATTERN


def test_kotlin_controller_contract_matches_the_server() -> None:
    """The UUID-like pattern and dedicated header must remain exact literals."""
    source = _kotlin("CommandClient.kt")
    pattern = re.search(r'CONTROLLER_ID_PATTERN\s*:\s*Regex\s*=\s*Regex\("([^"]+)"\)', source)
    header = re.search(r'CONTROLLER_ID_HEADER\s*:\s*String\s*=\s*"([^"]+)"', source)
    assert pattern, "could not find CONTROLLER_ID_PATTERN in the Kotlin client"
    assert header, "could not find CONTROLLER_ID_HEADER in the Kotlin client"
    assert pattern.group(1) == _EXPECTED_CONTROLLER_ID_PATTERN
    assert header.group(1) == _EXPECTED_CONTROLLER_ID_HEADER

    try:
        from animator.models.controller_lease import CONTROLLER_ID_HEADER, CONTROLLER_ID_PATTERN
    except ImportError:
        return
    assert CONTROLLER_ID_PATTERN == _EXPECTED_CONTROLLER_ID_PATTERN
    assert CONTROLLER_ID_HEADER == _EXPECTED_CONTROLLER_ID_HEADER


def test_kotlin_lease_model_names_every_response_field() -> None:
    """The no-toolchain suite covers the lease model before server integration."""
    source = _kotlin("ApiModels.kt")
    payload = _fixture("controller-lease.json")
    serial_names = _serial_names(source)
    properties = _property_names(source)
    missing = sorted(key for key in payload if key not in serial_names and _camel(key) not in properties)
    assert not missing


def test_kotlin_unusable_state_detail_matches_the_server() -> None:
    """The one recoverable corrupt-state detail is compared verbatim.

    The remote offers "Rebuild state" only for this exact detail string, so a
    reworded server message would silently strip the operator's only recovery from
    an unreadable ceremony.
    """
    expected = [entry[2] for entry in _ERROR_MAP if entry[1] == 500]
    assert expected, "the control error map no longer maps any exception to a 500"

    source = _kotlin("CommandClient.kt")
    match = re.search(r'UNUSABLE_STATE_DETAIL\s*:\s*String\s*=\s*"([^"]+)"', source)
    assert match, "could not find UNUSABLE_STATE_DETAIL in the Kotlin client"
    assert match.group(1) in expected


def test_kotlin_definitive_statuses_match_the_stated_refusals() -> None:
    """The client's "nothing was applied" set matches the documented statuses.

    ``400/403/404/409/422`` are the refusals the server states; everything else,
    including the store's ``503``, must stay ambiguous so a command whose outcome
    is unknown can never be replayed into a double reveal.
    """
    source = _kotlin("CommandClient.kt")
    match = re.search(r"DEFINITIVE_STATUSES\s*:\s*Set<Int>\s*=\s*setOf\(([^)]*)\)", source)
    assert match, "could not find DEFINITIVE_STATUSES in the Kotlin client"
    statuses = {int(value.strip()) for value in match.group(1).split(",") if value.strip()}
    assert statuses == {400, 403, 404, 409, 422}


def test_jump_pending_is_wired_through_every_android_layer() -> None:
    """The server command remains reachable from the Android button pad."""
    assert 'jumpPending = "$base/jump-pending"' in _kotlin("Urls.kt")
    assert "suspend fun jumpPending()" in _kotlin("CommandClient.kt")
    assert "val jumpPendingVisible: Boolean" in _kotlin("Controls.kt")
    assert "fun jumpPending()" in _kotlin("../ui/RemoteViewModel.kt")
    assert 'Text("Jump to next pending")' in _kotlin("../ui/CommandPad.kt")
