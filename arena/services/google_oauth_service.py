#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Google OpenID Connect client for the Arena login door.

This module owns the conversation with Google and nothing else: the database
side of a linked identity lives in :mod:`arena.services.google_identity_service`.

The flow is delegated to Authlib rather than hand-rolled, because the parts that
must not be got wrong -- ``state``, ``nonce``, PKCE, and above all verifying the
ID token's signature against Google's *rotating* JWKS -- are exactly the parts
Authlib's OIDC discovery integration owns (PKCE only once asked for; see
``GOOGLE_CODE_CHALLENGE_METHOD``). ``authorize_access_token`` populates
``token["userinfo"]`` only after that verification succeeds, so claims read from
it have been checked for signature, issuer, audience and nonce.

This deliberately does not go through ``shared.services.network_utils``: that
helper is a synchronous, SSRF-validating ``requests`` wrapper for arbitrary
operator-supplied URLs, whereas Google's endpoints are fixed and public and this
flow needs async plus Authlib's JWKS caching.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from authlib.integrations.starlette_client import OAuth
from fastapi import Request

from arena.config import Settings

logger = logging.getLogger(__name__)

GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
GOOGLE_SCOPES = "openid email profile"

# PKCE is opt-in, not automatic: Authlib emits a code_challenge only when the
# client declares a challenge method, so leaving this unset silently produces a
# plain authorization-code flow with no proof of possession on the exchange.
GOOGLE_CODE_CHALLENGE_METHOD = "S256"

_CLIENT_NAME = "google"


@dataclass(frozen=True)
class GoogleIdentityClaims:
    """The subset of verified Google ID-token claims Arena acts on.

    Attributes:
        sub: Google's stable subject identifier. The join key for a linked
            identity, because a Google account's email address can change while
            this cannot.
        email: The Google account email, used only for display and for detecting
            a collision with an existing Arena account.
        email_verified: Whether Google asserts it verified that address.
        name: The account holder's name, used to seed a Google-first signup.
        picture: Optional URL of the account holder's profile picture.
    """

    sub: str
    email: str | None
    email_verified: bool
    name: str | None
    picture: str | None = None


def build_google_oauth_client(settings: Settings) -> Any | None:
    """Build the Authlib Google client, or None when the feature is disabled.

    Args:
        settings: Arena settings carrying the OAuth toggle and credentials.

    Returns:
        Any | None: The registered Authlib Starlette client, or ``None`` when
            ``GOOGLE_OAUTH_ENABLED`` is false. Callers treat ``None`` as "this
            deployment has no Google door" and answer 404.
    """
    if not settings.GOOGLE_OAUTH_ENABLED:
        return None

    oauth = OAuth()
    oauth.register(
        name=_CLIENT_NAME,
        client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
        client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
        server_metadata_url=GOOGLE_DISCOVERY_URL,
        client_kwargs={
            "scope": GOOGLE_SCOPES,
            "code_challenge_method": GOOGLE_CODE_CHALLENGE_METHOD,
        },
    )
    logger.info("Google OAuth client registered for Arena login")
    return oauth.create_client(_CLIENT_NAME)


def google_redirect_uri(request: Request, url_base: str) -> str:
    """Build the absolute redirect URI Google must be configured with.

    Derived from the deployment's own public base URL rather than configured
    separately, so the two cannot drift apart. ``url_base`` is expected to be
    ``settings.ARENA_URL_BASE`` when set and the request's base URL otherwise --
    the same rule the consent emails already follow -- because a bare
    ``request.url_for`` yields ``http://`` behind a TLS-terminating proxy.

    Args:
        request: The active request, used only to resolve the callback path.
        url_base: Public base URL of this Arena deployment, without a trailing slash.

    Returns:
        str: Absolute HTTPS (or HTTP, in development) callback URL.
    """
    return f"{url_base.rstrip('/')}{request.url_for('arena_google_callback').path}"


def extract_claims(token: dict[str, Any]) -> GoogleIdentityClaims | None:
    """Read the verified identity claims out of an Authlib token response.

    Args:
        token: The mapping returned by ``authorize_access_token``.

    Returns:
        GoogleIdentityClaims | None: The claims, or ``None`` when the response
            carried no ``userinfo`` or no ``sub`` -- which means the ID token was
            absent or unverifiable, and the caller must refuse the login.
    """
    userinfo = token.get("userinfo")
    if not isinstance(userinfo, dict):
        logger.warning("Google token response carried no verified userinfo claims")
        return None

    sub = userinfo.get("sub")
    if not isinstance(sub, str) or not sub:
        logger.warning("Google token response carried no subject claim")
        return None

    email = userinfo.get("email")
    name = userinfo.get("name")
    picture = userinfo.get("picture")
    return GoogleIdentityClaims(
        sub=sub,
        email=email if isinstance(email, str) and email else None,
        email_verified=bool(userinfo.get("email_verified")),
        name=name if isinstance(name, str) and name else None,
        picture=picture if isinstance(picture, str) and picture else None,
    )
