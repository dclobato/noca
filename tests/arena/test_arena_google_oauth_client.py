#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The *real* Authlib client, not a stub.

Every other Google test stubs the OAuth client, which is right for exercising
Arena's own decisions but blind to how the client is configured. That blindness
is not hypothetical: PKCE is opt-in in Authlib -- it emits a ``code_challenge``
only when the client declares a challenge method -- so the flow ran without it
until a check like this one looked at the actual authorization URL.

These tests therefore assert the protocol parameters Arena is relying on, by
building the client the application builds and reading the redirect it produces.
Google's discovery document is stubbed so nothing here reaches the network.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from arena.config import Settings
from arena.services import google_oauth_service

_REDIRECT_URI = "https://arena.example/auth/google/callback"

_DISCOVERY = {
    "issuer": "https://accounts.google.com",
    "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_endpoint": "https://oauth2.googleapis.com/token",
    "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
    "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
}


def _enabled_settings() -> Settings:
    """Build settings with Google sign-in configured.

    The fields carry ``validation_alias``, so they must be supplied under their
    ``NOCA_ARENA_*`` names; passing the field names would be silently ignored.
    """
    return Settings(
        NOCA_ARENA_GOOGLE_OAUTH_ENABLED=True,
        NOCA_ARENA_GOOGLE_OAUTH_CLIENT_ID="test-client-id",
        NOCA_ARENA_GOOGLE_OAUTH_CLIENT_SECRET="test-client-secret",
    )


async def _authorization_url() -> str:
    """Return the URL the real client redirects a user to, without any network I/O."""
    client = google_oauth_service.build_google_oauth_client(_enabled_settings())
    assert client is not None

    async def _metadata(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return _DISCOVERY

    client.load_server_metadata = _metadata  # type: ignore[method-assign]

    captured: dict[str, str] = {}

    async def _start(request: Request) -> Response:
        response = await client.authorize_redirect(request, _REDIRECT_URI)
        captured["location"] = response.headers["location"]
        return response

    app = Starlette(routes=[Route("/start", _start)])
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        await http.get("/start", follow_redirects=False)

    return captured["location"]


def _disabled_settings() -> Settings:
    """Build settings with Google sign-in explicitly off.

    The toggle is stated rather than left to the default, because ``Settings``
    reads the developer's own ``.env`` and a machine configured for a live Google
    test would otherwise make this assertion vacuous.
    """
    return Settings(NOCA_ARENA_GOOGLE_OAUTH_ENABLED=False)


@pytest.mark.asyncio
async def test_the_client_is_none_while_the_feature_is_disabled() -> None:
    """A disabled deployment builds no client, which is what makes the routes 404."""
    assert google_oauth_service.build_google_oauth_client(_disabled_settings()) is None


@pytest.mark.asyncio
async def test_the_authorization_request_carries_pkce() -> None:
    """PKCE is opt-in in Authlib; without it the code exchange has no proof of possession."""
    params = parse_qs(urlparse(await _authorization_url()).query)

    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"], "a challenge method with no challenge is not PKCE"


@pytest.mark.asyncio
async def test_the_authorization_request_carries_state_and_nonce() -> None:
    """`state` defends the callback against CSRF; `nonce` binds the ID token to it."""
    params = parse_qs(urlparse(await _authorization_url()).query)

    assert params["state"][0]
    assert params["nonce"][0]


@pytest.mark.asyncio
async def test_the_authorization_request_asks_only_for_the_scopes_arena_uses() -> None:
    """Arena reads `sub`, `email`, `email_verified` and `name` -- and nothing else."""
    params = parse_qs(urlparse(await _authorization_url()).query)

    assert sorted(params["scope"][0].split()) == ["email", "openid", "profile"]


@pytest.mark.asyncio
async def test_the_authorization_request_uses_the_supplied_redirect_uri() -> None:
    """The redirect URI is derived per request, so it must reach Google unchanged."""
    params = parse_qs(urlparse(await _authorization_url()).query)

    assert params["redirect_uri"] == [_REDIRECT_URI]
    assert params["client_id"] == ["test-client-id"]
    assert params["response_type"] == ["code"]
