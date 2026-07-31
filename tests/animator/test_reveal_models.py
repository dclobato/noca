#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Pure tests for the reveal-session state contract.

These tests touch no database: they pin serialization, versioning, deep
immutability, the validated transition path, and the rule that no derived value
(rank, attempts, penalty) may ever enter persisted state.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from animator.models.command_receipt import MAX_COMMAND_RECEIPTS, CommandReceipt, append_receipt
from animator.models.reveal_session import (
    REVEAL_STATE_VERSION,
    MedalCutoffs,
    RevealSessionState,
    RevealStateVersionError,
    StepEntry,
)


def make_state(**changes: Any) -> RevealSessionState:
    """Build a valid site-scoped state, overriding the given fields."""
    payload: dict[str, Any] = {
        "contest_id": "contest-1",
        "site_id": "site-1",
        "site_name": "Campus A",
        "phase": "idle",
        "medal_cutoffs": {"gold": 1, "silver": 2, "bronze": 3},
        "frozen_submission_ids": ("s1", "s2", "s3"),
        "step_log": (),
        "focused_team_id": None,
    }
    payload.update(changes)
    # These tests describe *scoring* situations, so they name the reveals and let
    # the helper build the equivalent trail — the same translation the model's
    # own `with_reveal_log` performs.
    revealed = payload.pop("reveal_log", None)
    if revealed is not None:
        payload["step_log"] = tuple(StepEntry.reveal(item) for item in revealed)
    return RevealSessionState.model_validate(payload)


def make_global_state(**changes: Any) -> RevealSessionState:
    """Build a valid global state, overriding the given fields."""
    return make_state(site_id=None, site_name=None, medal_cutoffs=None, **changes)


# ---------------------------------------------------------------------------
# Serialization and versioning
# ---------------------------------------------------------------------------


def test_payload_round_trip_preserves_state() -> None:
    state = make_state(reveal_log=("s2",), phase="revealing", focused_team_id="team-1")
    payload = state.to_payload()

    assert payload["state_version"] == REVEAL_STATE_VERSION
    assert payload["frozen_submission_ids"] == ["s1", "s2", "s3"]
    assert payload["step_log"] == [{"kind": "reveal", "submission_id": "s2"}]
    assert RevealSessionState.from_payload(payload) == state


def test_from_payload_rejects_incompatible_version() -> None:
    future = REVEAL_STATE_VERSION + 1
    payload = make_state().to_payload()
    payload["state_version"] = future

    with pytest.raises(RevealStateVersionError) as excinfo:
        RevealSessionState.from_payload(payload)

    assert excinfo.value.version == future
    assert str(future) in str(excinfo.value)


def test_from_payload_rejects_the_previous_format() -> None:
    """A version-1 payload is a ceremony recorded before the cursor existed.

    Its trail was a list of reveals with no cursor, so it cannot be replayed as a
    step trail — reading it would silently place the cursor at the bottom row and
    re-walk rows the operator had already passed. It is refused like any other
    foreign version; the operator recovers with ``start-reveal`` + ``restart``.
    """
    payload = make_state(reveal_log=("s1",)).to_payload()
    payload["state_version"] = 1
    payload["reveal_log"] = ["s1"]
    del payload["step_log"]

    with pytest.raises(RevealStateVersionError) as excinfo:
        RevealSessionState.from_payload(payload)

    assert excinfo.value.version == 1


def test_from_payload_rejects_the_receiptless_format() -> None:
    """A version-2 payload predates command receipts, so it cannot be read as one.

    Accepting it would present a ceremony as having applied no commands, and the
    first retry after an upgrade would then apply a second time — the exact
    failure receipts exist to prevent.
    """
    payload = make_state().to_payload()
    payload["state_version"] = 2
    del payload["command_receipts"]

    with pytest.raises(RevealStateVersionError) as excinfo:
        RevealSessionState.from_payload(payload)

    assert excinfo.value.version == 2


def test_from_payload_rejects_an_unversioned_payload() -> None:
    """An unversioned payload is legacy or corrupt, never silently current."""
    payload = make_state().to_payload()
    del payload["state_version"]

    with pytest.raises(RevealStateVersionError) as excinfo:
        RevealSessionState.from_payload(payload)

    assert excinfo.value.version is None
    assert "no version" in str(excinfo.value)


def test_from_payload_rejects_a_corrupt_version() -> None:
    payload = make_state().to_payload()
    payload["state_version"] = "1"

    with pytest.raises(RevealStateVersionError):
        RevealSessionState.from_payload(payload)


def test_model_validate_rejects_incompatible_version() -> None:
    payload = make_state().to_payload()
    payload["state_version"] = REVEAL_STATE_VERSION + 1

    with pytest.raises(ValidationError):
        RevealSessionState.model_validate(payload)


# ---------------------------------------------------------------------------
# Derived values are not state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["rank", "attempts", "penalty", "standings", "solved"])
def test_derived_fields_are_rejected(field: str) -> None:
    payload = make_state().to_payload()
    payload[field] = 1

    with pytest.raises(ValidationError):
        RevealSessionState.model_validate(payload)


def test_state_declares_only_identity_and_the_step_trail() -> None:
    assert set(RevealSessionState.model_fields) == {
        "state_version",
        "contest_id",
        "site_id",
        "site_name",
        "phase",
        "medal_cutoffs",
        "frozen_submission_ids",
        "step_log",
        "focused_team_id",
        "command_receipts",
    }


def test_the_revealed_set_and_the_cursor_are_derived_not_stored() -> None:
    """Both come from the one trail, so ``back()`` cannot desynchronize them."""
    state = make_state().with_reveal("s1").with_advance().with_reveal("s2")

    assert (state.reveal_log, state.cursor) == (("s1", "s2"), 1)
    assert "reveal_log" not in state.to_payload()
    assert "cursor" not in state.to_payload()

    # Undoing walks back through both kinds of step, in order.
    once = state.without_last_step()
    assert (once.reveal_log, once.cursor) == (("s1",), 1)
    twice = once.without_last_step()
    assert (twice.reveal_log, twice.cursor) == (("s1",), 0)


# ---------------------------------------------------------------------------
# Deep immutability
# ---------------------------------------------------------------------------


def test_fields_cannot_be_reassigned() -> None:
    state = make_state()

    with pytest.raises(ValidationError):
        state.phase = "done"  # type: ignore[misc]


def test_collection_fields_are_tuples_and_cannot_be_mutated() -> None:
    state = make_state(reveal_log=("s1",))

    assert isinstance(state.frozen_submission_ids, tuple)
    assert isinstance(state.reveal_log, tuple)
    with pytest.raises(AttributeError):
        state.reveal_log.append("s2")  # type: ignore[attr-defined]


def test_every_persisted_field_value_is_immutable() -> None:
    state = make_state(reveal_log=("s1",))

    for name in RevealSessionState.model_fields:
        hash(getattr(state, name))


# ---------------------------------------------------------------------------
# Validated transitions
# ---------------------------------------------------------------------------


def test_with_reveal_log_appends_and_pops_reversibly() -> None:
    state = make_state()
    stepped = state.with_reveal_log((*state.reveal_log, "s1"))
    back = stepped.with_reveal_log(stepped.reveal_log[:-1])

    assert stepped.reveal_log == ("s1",)
    assert back == state


def test_with_updates_validates_the_new_reveal_log() -> None:
    state = make_state(reveal_log=("s1",))

    with pytest.raises(ValidationError):
        state.with_reveal_log(("s1", "s1"))
    with pytest.raises(ValidationError):
        state.with_reveal_log(("s1", "unknown"))


def test_model_copy_update_bypasses_validation() -> None:
    """Why ``with_updates`` exists: ``model_copy`` does not validate updates."""
    state = make_state()
    duplicated = tuple(StepEntry.reveal(item) for item in ("s1", "s1", "unknown"))
    unchecked = state.model_copy(update={"step_log": duplicated})

    assert unchecked.reveal_log == ("s1", "s1", "unknown")
    with pytest.raises(ValidationError):
        RevealSessionState.model_validate(unchecked.model_dump())


def test_with_phase_and_focus_return_new_states() -> None:
    state = make_state()
    moved = state.with_phase("revealing").with_focused_team("team-9")

    assert state.phase == "idle" and state.focused_team_id is None
    assert moved.phase == "revealing"
    assert moved.focused_team_id == "team-9"


# ---------------------------------------------------------------------------
# Log invariants
# ---------------------------------------------------------------------------


def test_reveal_log_must_be_a_subset_of_the_frozen_universe() -> None:
    with pytest.raises(ValidationError):
        make_state(reveal_log=("nope",))


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(ValidationError):
        make_state(frozen_submission_ids=("s1", "s1"))
    with pytest.raises(ValidationError):
        make_state(reveal_log=("s1", "s1"))


# ---------------------------------------------------------------------------
# Scope invariant
# ---------------------------------------------------------------------------


def test_global_and_site_scopes_are_accepted() -> None:
    assert make_global_state().site_id is None
    assert make_state().medal_cutoffs == MedalCutoffs(gold=1, silver=2, bronze=3)


@pytest.mark.parametrize(
    "changes",
    [
        {"site_id": None, "medal_cutoffs": None},
        {"site_id": None, "site_name": None},
        {"site_name": None},
        {"medal_cutoffs": None},
    ],
    ids=["global-with-name", "global-with-cutoffs", "site-without-name", "site-without-cutoffs"],
)
def test_half_populated_scopes_are_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_state(**changes)


# ---------------------------------------------------------------------------
# Medal cutoffs
# ---------------------------------------------------------------------------


def test_cutoffs_must_be_positive_and_ordered() -> None:
    MedalCutoffs(gold=4, silver=8, bronze=12)
    MedalCutoffs(gold=1, silver=1, bronze=1)

    with pytest.raises(ValidationError):
        MedalCutoffs(gold=0, silver=2, bronze=3)
    with pytest.raises(ValidationError):
        MedalCutoffs(gold=3, silver=2, bronze=4)
    with pytest.raises(ValidationError):
        MedalCutoffs(gold=1, silver=5, bronze=4)


def test_cutoffs_reject_extra_fields() -> None:
    with pytest.raises(ValidationError):
        MedalCutoffs.model_validate({"gold": 1, "silver": 2, "bronze": 3, "platinum": 0})


# ---------------------------------------------------------------------------
# Command receipts
# ---------------------------------------------------------------------------


def _receipt(index: int) -> CommandReceipt:
    """Build a receipt whose key encodes its position, for ordering assertions."""
    return CommandReceipt(key=f"ring-key-{index:04d}", command="step")


def test_receipts_survive_a_payload_round_trip() -> None:
    state = make_global_state().with_receipt(_receipt(1))
    restored = RevealSessionState.from_payload_json(state.to_payload_json())
    assert restored == state
    assert restored.state_version == REVEAL_STATE_VERSION
    assert restored.command_receipts[-1].key == "ring-key-0001"


def test_receipt_ring_keeps_the_newest_and_drops_the_oldest() -> None:
    state = make_global_state()
    for index in range(MAX_COMMAND_RECEIPTS + 3):
        state = state.with_receipt(_receipt(index))

    keys = [receipt.key for receipt in state.command_receipts]
    assert len(keys) == MAX_COMMAND_RECEIPTS
    assert keys[-1] == _receipt(MAX_COMMAND_RECEIPTS + 2).key, "the newest command must never be evicted"
    assert keys[0] == _receipt(3).key


def test_only_the_newest_receipt_is_replayable() -> None:
    state = make_global_state().with_receipt(_receipt(1)).with_receipt(_receipt(2))
    assert state.is_latest_receipt(_receipt(2).key)
    assert not state.is_latest_receipt(_receipt(1).key), "an older command can no longer be reproduced"
    assert state.receipt_for(_receipt(1).key) is not None, "but it is still known to have been applied"
    assert state.receipt_for("ring-key-9999") is None


def test_duplicate_receipt_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        make_global_state(command_receipts=(_receipt(1), _receipt(1)))


def test_receipt_ring_cannot_exceed_its_bound() -> None:
    overfull = tuple(_receipt(index) for index in range(MAX_COMMAND_RECEIPTS + 1))
    with pytest.raises(ValidationError):
        make_global_state(command_receipts=overfull)


def test_malformed_receipt_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        CommandReceipt(key="short", command="step")
    with pytest.raises(ValidationError):
        CommandReceipt(key="has spaces here", command="step")


def test_append_receipt_is_pure() -> None:
    ring = (_receipt(1),)
    grown = append_receipt(ring, _receipt(2))
    assert ring == (_receipt(1),), "the input ring is never mutated"
    assert grown == (_receipt(1), _receipt(2))
