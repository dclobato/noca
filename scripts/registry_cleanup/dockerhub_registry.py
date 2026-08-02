#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Manifest deletion on Docker Hub through the OCI registry API.

Deleting a tag through the Hub API removes only the *tag*. The image index it
pointed at survives, untagged, still counted as active storage — Docker Hub does
not cascade the way its Image Management UI does. Reclaiming that space needs a
second step against the registry itself: ``DELETE /v2/{name}/manifests/{digest}``
on ``registry-1.docker.io``.

The registry enforces safety on its own: only untagged or otherwise unreferenced
manifests can be deleted, and a manifest still referenced by a tag or another
image answers ``403 Forbidden``. This client therefore never has to reason about
reachability — it asks, and a refusal is a correct answer rather than an error.

Deletion is also asynchronous. Docker documents that the call may answer ``500``
while the removal is retried in the background, so that status is reported as
*pending* rather than as a failure.

Authentication is the standard registry token flow against ``auth.docker.io``,
with the personal access token supplied as basic-auth credentials and the
``delete`` scope requested alongside ``pull``.
"""

from __future__ import annotations

import enum

import httpx

_AUTH_URL = "https://auth.docker.io/token"
_REGISTRY_URL = "https://registry-1.docker.io"
_TIMEOUT = httpx.Timeout(60.0)


class DockerRegistryError(RuntimeError):
    """Raised when the registry cannot be reached or authenticated."""


class DeleteOutcome(enum.Enum):
    """The result of asking the registry to delete one manifest."""

    DELETED = "deleted"
    PENDING = "queued by the registry"
    STILL_REFERENCED = "still referenced by a tag"
    ABSENT = "already gone"


class DockerHubRegistryClient:
    """Deletes manifests from Docker Hub by digest."""

    def __init__(self, username: str, token: str) -> None:
        """Prepare the registry client.

        Args:
            username: Docker Hub account name.
            token: Personal access token with delete permission.
        """
        self._username = username
        self._token = token
        self._client = httpx.Client(timeout=_TIMEOUT)
        self._scoped_tokens: dict[str, str] = {}

    def __enter__(self) -> DockerHubRegistryClient:
        """Enter the context manager."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def delete_manifest(self, repository: str, digest: str) -> DeleteOutcome:
        """Delete one manifest by digest.

        Args:
            repository: The ``namespace/name`` repository reference.
            digest: The manifest digest to delete.

        Returns:
            What the registry did with the request.

        Raises:
            DockerRegistryError: If the registry answered with an unexpected
                status, or no delete-scoped token could be obtained.
        """
        response = self._client.delete(
            f"{_REGISTRY_URL}/v2/{repository}/manifests/{digest}",
            headers={"Authorization": f"Bearer {self._scoped_token(repository)}"},
        )
        if response.status_code in (httpx.codes.ACCEPTED, httpx.codes.OK):
            return DeleteOutcome.DELETED
        if response.status_code == httpx.codes.NOT_FOUND:
            return DeleteOutcome.ABSENT
        if response.status_code == httpx.codes.FORBIDDEN:
            return DeleteOutcome.STILL_REFERENCED
        if response.status_code == httpx.codes.INTERNAL_SERVER_ERROR:
            # Documented behavior: deletion is retried in the background.
            return DeleteOutcome.PENDING
        raise DockerRegistryError(f"Deleting {repository}@{digest[:19]} failed ({response.status_code})")

    def _scoped_token(self, repository: str) -> str:
        """Obtain (and cache) a delete-scoped token for one repository.

        Args:
            repository: The ``namespace/name`` repository reference.

        Returns:
            The bearer token to use against the registry.

        Raises:
            DockerRegistryError: If the token endpoint refuses the credentials.
        """
        cached = self._scoped_tokens.get(repository)
        if cached is not None:
            return cached

        response = self._client.get(
            _AUTH_URL,
            params={
                "service": "registry.docker.io",
                "scope": f"repository:{repository}:pull,delete",
            },
            auth=(self._username, self._token),
        )
        if response.status_code != httpx.codes.OK:
            raise DockerRegistryError(f"Registry auth for {repository} failed ({response.status_code})")
        issued = response.json().get("token")
        if not issued:
            raise DockerRegistryError(f"Registry auth for {repository} returned no token")
        self._scoped_tokens[repository] = str(issued)
        return str(issued)
