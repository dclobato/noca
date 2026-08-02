#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""OCI registry reads against ``ghcr.io``, used to resolve manifest parentage.

The GitHub Packages API describes a package version as a digest plus its tags,
but it never says which digests are *children* of which. Multi-platform images
make that the decisive question: publishing one release creates one index
manifest carrying the release tags plus one untagged child manifest per platform
(and one more per BuildKit attestation), and the API reports every child as an
ordinary untagged version.

Deleting a tagged index therefore orphans its children, and no age or download
count distinguishes those orphans from the children of a *live* release. The
registry API answers it exactly: fetching a surviving index manifest lists the
digests it references, so anything untagged and unreferenced is genuinely
unreachable.

Reads use the standard OCI token flow: a pull token is fetched per repository
from ``ghcr.io/token`` with the personal access token as basic-auth credentials,
falling back to GHCR's base64 bearer scheme when that endpoint refuses.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

_REGISTRY_URL = "https://ghcr.io"
_TIMEOUT = httpx.Timeout(30.0)

#: Media types that must be accepted to receive an index rather than a
#: converted single-platform manifest.
_MANIFEST_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)


class RegistryError(RuntimeError):
    """Raised when the registry cannot be read for a repository."""


class GHCRRegistryClient:
    """Reads manifests from ``ghcr.io`` to resolve index parentage."""

    def __init__(self, owner: str, token: str) -> None:
        """Prepare the registry client.

        Args:
            owner: The GHCR namespace owning the packages.
            token: A GitHub token with at least ``read:packages``.
        """
        self._owner = owner
        self._token = token
        self._client = httpx.Client(base_url=_REGISTRY_URL, timeout=_TIMEOUT)
        self._pull_tokens: dict[str, str] = {}

    def __enter__(self) -> GHCRRegistryClient:
        """Enter the context manager."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def child_digests(self, package: str, digest: str) -> set[str]:
        """Return the digests one manifest references.

        Args:
            package: The package name, for example ``noca/rating``.
            digest: The manifest digest to inspect.

        Returns:
            The child digests of an index manifest, or an empty set for a
            single-platform image manifest.

        Raises:
            RegistryError: If the manifest cannot be read. Callers must treat
                this as "parentage unknown" and skip orphan cleanup, because a
                missing answer here is indistinguishable from "no children".
        """
        repository = f"{self._owner}/{package}"
        response = self._client.get(
            f"/v2/{repository}/manifests/{digest}",
            headers={
                "Accept": _MANIFEST_ACCEPT,
                "Authorization": f"Bearer {self._pull_token(repository)}",
            },
        )
        if response.status_code != httpx.codes.OK:
            raise RegistryError(f"Reading manifest {package}@{digest[:19]} failed ({response.status_code})")

        manifest: dict[str, Any] = response.json()
        children = manifest.get("manifests")
        if not isinstance(children, list):
            return set()
        return {str(child["digest"]) for child in children if "digest" in child}

    def _pull_token(self, repository: str) -> str:
        """Obtain (and cache) a pull token for one repository.

        Args:
            repository: The full ``owner/package`` registry path.

        Returns:
            The bearer token to use for manifest reads.

        Raises:
            RegistryError: If no usable token could be obtained.
        """
        cached = self._pull_tokens.get(repository)
        if cached is not None:
            return cached

        response = self._client.get(
            "/token",
            params={"service": "ghcr.io", "scope": f"repository:{repository}:pull"},
            auth=(self._owner, self._token),
        )
        if response.status_code == httpx.codes.OK:
            issued = response.json().get("token") or response.json().get("access_token")
            if issued:
                self._pull_tokens[repository] = str(issued)
                return str(issued)

        # GHCR also accepts the base64-encoded PAT directly as a bearer token.
        fallback = base64.b64encode(self._token.encode()).decode()
        self._pull_tokens[repository] = fallback
        return fallback
