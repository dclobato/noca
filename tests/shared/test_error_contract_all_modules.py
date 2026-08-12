#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The error-response contract, asserted against the real applications.

Every other error-handler test builds a small app and registers handlers by hand,
which proves the handler logic but not that ``main.py`` actually wires it up.
This drives ``web.main.app``, ``arena.main.app``, ``animator.main.app`` and
``healthmonitor.main.app`` directly, so a module that forgets to call
``register_error_handlers`` fails here.

No database or Valkey is needed: a router 404/405 is produced during routing,
before any dependency runs, so these requests never reach application code.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import animator.main
import arena.main
import healthmonitor.main
import web.main

pytestmark = pytest.mark.asyncio

# Every module exposes GET /health, so POSTing to it exercises the router's own
# 405 without touching a handler.
_GET_ONLY_PATH = "/health"


def _apps() -> list[tuple[str, FastAPI]]:
    return [
        ("web", web.main.app),
        ("arena", arena.main.app),
        ("animator", animator.main.app),
        ("healthmonitor", healthmonitor.main.app),
    ]


_APP_IDS = [name for name, _ in _apps()]


@pytest.fixture(autouse=True)
def _minimal_middleware_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Satisfy the middleware attributes that ``lifespan`` would normally set.

    Web and Arena install auth middleware that reads ``app.state.jwt_service`` /
    ``app.state.auth_service`` before routing.  Both are looked up
    unconditionally but only *used* when the request carries an auth cookie, and
    these requests carry none -- so a sentinel is enough.  That keeps the test
    free of a database, Valkey, and the full startup sequence while still
    exercising the real registered exception handlers.

    ``app`` here is the process-wide application singleton, so the sentinels are
    installed through ``monkeypatch`` and removed at teardown.  Setting them
    directly would leave them on global state for every later test in the same
    process -- and a stale sentinel is worse than a missing attribute, because a
    test that should fail loudly on absent state would instead run against an
    object that answers nothing.  ``raising=False`` records "was not set", which
    ``State.__delattr__`` then undoes; an attribute a real lifespan already
    populated is left alone by the ``hasattr`` check.
    """
    for _, app in _apps():
        for attribute in ("jwt_service", "auth_service"):
            if not hasattr(app.state, attribute):
                monkeypatch.setattr(app.state, attribute, object(), raising=False)


async def _get(app: FastAPI, path: str, **kwargs: object) -> object:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(kwargs.pop("method", "GET"), path, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("module_name,app", _apps(), ids=_APP_IDS)
async def test_unknown_path_is_neutral_in_every_module(module_name: str, app: FastAPI) -> None:
    """No module may answer a 404 with the framework's own body shape."""
    response = await _get(app, "/__definitely_not_a_route__", headers={"accept": "application/json"})

    assert response.status_code == 404  # type: ignore[attr-defined]
    assert response.json() == {"error": "not_found"}, module_name  # type: ignore[attr-defined]


@pytest.mark.parametrize("module_name,app", _apps(), ids=_APP_IDS)
async def test_wrong_method_is_neutral_and_keeps_allow(module_name: str, app: FastAPI) -> None:
    """A 405 must be neutral *and* still carry the Allow header.

    Rewriting the body must not drop a protocol-required header: RFC 9110 makes
    ``Allow`` mandatory on a 405, and the router already computed it.
    """
    response = await _get(app, _GET_ONLY_PATH, method="POST", headers={"accept": "application/json"})

    assert response.status_code == 405, module_name  # type: ignore[attr-defined]
    assert response.json() == {"error": "method_not_allowed"}, module_name  # type: ignore[attr-defined]
    allow = response.headers.get("allow")  # type: ignore[attr-defined]
    assert allow is not None, f"{module_name}: 405 dropped the Allow header"
    assert "GET" in allow, module_name


@pytest.mark.parametrize("module_name,app", _apps(), ids=_APP_IDS)
async def test_no_module_leaks_the_framework_detail_shape(module_name: str, app: FastAPI) -> None:
    """The generic refusals must not contain a `detail` key at all."""
    for path, method in ((("/__nope__"), "GET"), ((_GET_ONLY_PATH), "POST")):
        response = await _get(app, path, method=method, headers={"accept": "application/json"})
        assert "detail" not in response.json(), f"{module_name} {method} {path}"  # type: ignore[attr-defined]


async def test_arena_oversized_path_does_not_raise_during_routing() -> None:
    """A path too long for CPython's int parser must not become a 500.

    Starlette's built-in ``:int`` convertor calls ``int(value)`` while matching,
    which raises above 4300 digits -- before any handler, so it escapes the
    exception handlers entirely.  The bounded ``:dbid`` convertor makes it a
    plain 404 instead.
    """
    for digits in (4300, 4301, 10_000):
        response = await _get(
            arena.main.app,
            "/problems/" + ("1" * digits),
            headers={"accept": "application/json"},
        )
        assert response.status_code == 404, f"{digits} digits -> {response.status_code}"  # type: ignore[attr-defined]
        assert response.json() == {"error": "not_found"}  # type: ignore[attr-defined]
