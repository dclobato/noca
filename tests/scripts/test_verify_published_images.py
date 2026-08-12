#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for published container-image verification."""

from __future__ import annotations

import httpx
import pytest

from scripts.verify_published_images import ManifestProbe, ManifestProbeError, Registry, ghcr_registry

_REPOSITORY = "dclobato/noca/landingpage"
_DIGEST = f"sha256:{'a' * 64}"


def _registry() -> Registry:
    """Build an isolated registry descriptor.

    Returns:
        A descriptor pointing at the mocked host.
    """
    return Registry(
        name="Test registry",
        api="https://registry.example.test",
        auth_url="https://registry.example.test/token",
        auth_service="registry.example.test",
        separator="/",
        credentials=None,
    )


def _transport(manifest_status: int) -> httpx.MockTransport:
    """Build a token-plus-manifest registry transport.

    Args:
        manifest_status: Status returned by the manifest endpoint.

    Returns:
        A deterministic mock transport.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"token": "pull-token"})
        return httpx.Response(
            manifest_status,
            headers={"Docker-Content-Digest": _DIGEST} if manifest_status == 200 else None,
        )

    return httpx.MockTransport(handler)


def test_manifest_probe_returns_none_only_for_a_missing_tag() -> None:
    """An explicit manifest 404 is the sole missing-tag signal."""
    with ManifestProbe(_registry(), _transport(404)) as probe:
        assert probe.digest(_REPOSITORY, "v17.0.0") is None


def test_manifest_probe_reports_private_package_authentication() -> None:
    """A private package without credentials is not described as missing."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/token"
        return httpx.Response(401)

    with (
        ManifestProbe(_registry(), httpx.MockTransport(handler)) as probe,
        pytest.raises(ManifestProbeError, match=r"authentication failed.*HTTP 401"),
    ):
        probe.digest(_REPOSITORY, "v17.0.0")


def test_manifest_probe_rejects_transient_registry_failure() -> None:
    """A registry outage is distinct from an absent tag."""
    with (
        ManifestProbe(_registry(), _transport(503)) as probe,
        pytest.raises(ManifestProbeError, match=r"manifest request failed with HTTP 503"),
    ):
        probe.digest(_REPOSITORY, "v17.0.0")


def test_manifest_probe_returns_registry_digest() -> None:
    """A successful manifest probe returns its canonical digest."""
    with ManifestProbe(_registry(), _transport(200)) as probe:
        assert probe.digest(_REPOSITORY, "v17.0.0") == _DIGEST


def test_ghcr_registry_uses_explicit_username_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Private GHCR packages authenticate as the configured account."""
    monkeypatch.setenv("GHCR_USERNAME", "registry-user")
    monkeypatch.setenv("GHCR_TOKEN", "registry-token")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    assert ghcr_registry().credentials == ("registry-user", "registry-token")
