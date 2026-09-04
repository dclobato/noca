#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Server-side half of the browser form-draft contract."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from shared.services.form_draft import (
    SESSION_KEY,
    confirm_form_draft,
    draft_owner_token,
    pop_confirmed_form_drafts,
    problem_definition_draft_key,
)


def _request(session: dict[str, Any] | None) -> Any:
    if session is None:

        class _NoSession:
            @property
            def session(self) -> dict[str, Any]:
                raise AssertionError("SessionMiddleware must be installed")

        return _NoSession()
    return SimpleNamespace(session=session)


def test_problem_definition_keys_name_the_form_stably() -> None:
    assert problem_definition_draft_key("arena", problem_id="p1") == "arena-problem-definition:p1"
    assert (
        problem_definition_draft_key("arena", validator_type="interactive")
        == "arena-problem-definition:new:interactive"
    )
    assert problem_definition_draft_key("web", contest_id="demo", problem_id="p1") == "web-problem-definition:demo:p1"
    assert (
        problem_definition_draft_key("web", contest_id="demo", validator_type="standard")
        == "web-problem-definition:demo:new:standard"
    )
    # An edit key ignores the strategy: it is fixed for an existing problem.
    assert problem_definition_draft_key("arena", problem_id="p1", validator_type="standard").endswith(":p1")
    with pytest.raises(ValueError):
        problem_definition_draft_key("arena")


def test_owner_token_is_opaque_stable_and_identity_sensitive() -> None:
    token = draft_owner_token("web", "team", "contest-1", "alice")
    assert token is not None and len(token) == 16 and "alice" not in token
    assert token == draft_owner_token("web", "team", "contest-1", "alice")
    assert token != draft_owner_token("web", "team", "contest-2", "alice")
    assert draft_owner_token("web", None, "c", "alice") is None
    assert draft_owner_token() is None


def test_confirmations_round_trip_through_the_session_once() -> None:
    session: dict[str, Any] = {}
    request = _request(session)
    confirm_form_draft(request, "a:1")
    confirm_form_draft(request, "b:2")
    confirm_form_draft(request, "a:1")
    assert session[SESSION_KEY] == ["b:2", "a:1"]
    assert pop_confirmed_form_drafts(request) == "b:2 a:1"
    assert SESSION_KEY not in session
    assert pop_confirmed_form_drafts(request) == ""


def test_confirmations_are_bounded_and_tolerate_garbage() -> None:
    session: dict[str, Any] = {SESSION_KEY: "not-a-list"}
    request = _request(session)
    for index in range(12):
        confirm_form_draft(request, f"k:{index}")
    assert len(session[SESSION_KEY]) == 8
    session[SESSION_KEY] = ["ok", 3, None, ""]
    assert pop_confirmed_form_drafts(request) == "ok"


def test_without_session_middleware_nothing_breaks() -> None:
    request = _request(None)
    confirm_form_draft(request, "a:1")
    assert pop_confirmed_form_drafts(request) == ""


def test_both_modules_derive_an_owner_from_their_real_token_shapes() -> None:
    """Arena tokens carry no audience, so the owner must not depend on one."""
    from arena.template_globals import form_draft_owner as arena_owner
    from web.template_globals import form_draft_owner as web_owner

    arena_token = SimpleNamespace(valid=True, aud=None, sub="user-1", extra_data=None)
    web_token = SimpleNamespace(valid=True, aud="team", sub="alice", extra_data={"contest_id": "c1"})
    assert arena_owner(SimpleNamespace(state=SimpleNamespace(validated_token=arena_token)))
    assert web_owner(SimpleNamespace(state=SimpleNamespace(validated_token=web_token)))
    assert arena_owner(SimpleNamespace(state=SimpleNamespace(validated_token=None))) is None
    assert web_owner(SimpleNamespace(state=SimpleNamespace())) is None
