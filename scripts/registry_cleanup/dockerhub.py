#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Docker Hub client for the image retention script.

Docker Hub deletes at *tag* granularity, which matches the policy: a tag that
falls out of retention is removed, and the floating ``latest`` / ``compile`` /
``run`` tags keep working even when their versioned siblings go away.

What deleting a tag does **not** do is reclaim any storage. The image index the
tag pointed at survives untagged and still counts as active — measured on
``noca-judge-rust``, 8 surviving tags referenced 6 indexes while the repository
held 21, the other 15 being leftovers of earlier tag deletions. That is why
:meth:`DockerHubClient.unreferenced_digests` reports which indexes a deletion
round strands, so :mod:`scripts.registry_cleanup.dockerhub_registry` can remove
them through the registry API.

Authentication exchanges a personal access token for a JWT at
``POST /v2/auth/token``; every later call carries it as a bearer token.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

_BASE_URL = "https://hub.docker.com"
_PAGE_SIZE = 100
_TIMEOUT = httpx.Timeout(30.0)


class DockerHubError(RuntimeError):
    """Raised when Docker Hub answers a request with an unexpected status."""


@dataclass(frozen=True)
class DockerHubTag:
    """One published tag and the image index digest it points at."""

    name: str
    digest: str


@dataclass(frozen=True)
class DockerHubRepository:
    """One repository inside a Docker Hub namespace."""

    namespace: str
    name: str

    @property
    def reference(self) -> str:
        """The ``namespace/name`` reference used for reporting."""
        return f"{self.namespace}/{self.name}"


class DockerHubClient:
    """Minimal Docker Hub API client covering listing and tag deletion."""

    def __init__(self, username: str, token: str) -> None:
        """Store credentials without contacting Docker Hub yet.

        Args:
            username: Docker Hub account name.
            token: Personal access token (or password) for that account.
        """
        self._username = username
        self._token = token
        self._client = httpx.Client(base_url=_BASE_URL, timeout=_TIMEOUT)
        self._authenticated = False

    def __enter__(self) -> DockerHubClient:
        """Enter the context manager, authenticating on the way in."""
        self.login()
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def login(self) -> None:
        """Exchange the personal access token for a bearer JWT.

        Raises:
            DockerHubError: If Docker Hub rejects the credentials.
        """
        response = self._client.post(
            "/v2/auth/token",
            json={"identifier": self._username, "secret": self._token},
        )
        if response.status_code != httpx.codes.OK:
            raise DockerHubError(f"Docker Hub login failed ({response.status_code})")
        access_token = response.json().get("access_token")
        if not access_token:
            raise DockerHubError("Docker Hub login returned no access token")
        self._client.headers["Authorization"] = f"Bearer {access_token}"
        self._authenticated = True

    def list_repositories(self, namespace: str) -> list[DockerHubRepository]:
        """List every repository of a namespace.

        Args:
            namespace: The Docker Hub namespace (user or organization).

        Returns:
            The repositories, in the order Docker Hub returns them.
        """
        url: str | None = f"/v2/repositories/{namespace}/?page_size={_PAGE_SIZE}"
        repositories: list[DockerHubRepository] = []
        while url:
            payload = self._get(url)
            for item in payload.get("results", []):
                repositories.append(DockerHubRepository(namespace=namespace, name=item["name"]))
            url = payload.get("next")
        return repositories

    def list_tags(self, repository: DockerHubRepository) -> list[DockerHubTag]:
        """List every tag published in a repository, with its index digest.

        The digest matters because deleting a tag leaves its image index behind:
        reclaiming that storage needs a second call against the registry API,
        and that call needs the digest the tag used to point at.

        Args:
            repository: The repository to inspect.

        Returns:
            The tags currently published.
        """
        url: str | None = (
            f"/v2/namespaces/{repository.namespace}/repositories/{repository.name}/tags?page_size={_PAGE_SIZE}"
        )
        tags: list[DockerHubTag] = []
        while url:
            payload = self._get(url)
            for item in payload.get("results", []):
                tags.append(DockerHubTag(name=item["name"], digest=str(item.get("digest", ""))))
            url = payload.get("next")
        return tags

    def delete_tag(self, repository: DockerHubRepository, tag: str) -> None:
        """Delete one tag.

        Args:
            repository: The repository owning the tag.
            tag: The tag name to delete.

        Raises:
            DockerHubError: If the deletion is refused. A tag that is already
                gone (``404``) is treated as success.
        """
        response = self._client.delete(
            f"/v2/namespaces/{repository.namespace}/repositories/{repository.name}/tags/{tag}"
        )
        accepted = {httpx.codes.NO_CONTENT, httpx.codes.OK, httpx.codes.ACCEPTED}
        if response.status_code in accepted or response.status_code == httpx.codes.NOT_FOUND:
            return
        raise DockerHubError(f"Deleting {repository.reference}:{tag} failed ({response.status_code})")

    @staticmethod
    def unreferenced_digests(tags: list[DockerHubTag], deleted: set[str]) -> list[str]:
        """Find the index digests left with no tag after a deletion round.

        Deleting a tag never removes the image index it pointed at, so each
        digest whose *entire* tag set was deleted becomes untagged storage that
        nothing can reach. A digest that keeps at least one tag — the usual case
        for a release digest shared with a floating tag — is left alone.

        Args:
            tags: Every tag the repository had before the deletions.
            deleted: The names of the tags that were deleted.

        Returns:
            The digests that no surviving tag references, without duplicates.
        """
        by_digest: dict[str, list[str]] = {}
        for tag in tags:
            if tag.digest:
                by_digest.setdefault(tag.digest, []).append(tag.name)
        return [digest for digest, names in by_digest.items() if all(name in deleted for name in names)]

    def _get(self, url: str) -> dict[str, Any]:
        """Issue an authenticated GET and decode the JSON body.

        Args:
            url: Absolute path or full URL to fetch.

        Returns:
            The decoded JSON object.

        Raises:
            DockerHubError: If the request was not answered with ``200``.
        """
        response = self._client.get(url)
        if response.status_code != httpx.codes.OK:
            raise DockerHubError(f"GET {url} failed ({response.status_code})")
        payload: dict[str, Any] = response.json()
        return payload
