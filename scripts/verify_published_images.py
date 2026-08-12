#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Verify that a release actually published every image tag it should have.

A publish run that dies part-way leaves a *partial* release, and nothing
notices: the floating tags keep serving the previous build and the missing
version pins are only discovered when someone tries to deploy them. That is
exactly how ``v15.0.1`` ended up with 15 of 21 languages missing their
``compile-v15.0.1`` / ``run-v15.0.1`` tags after one transient Docker Hub
``502`` aborted the Bake run.

This script asks each registry whether every expected tag resolves, and fails
loudly when one does not. It checks two things per image:

- the version-pinned tag exists at all;
- the floating tag (``latest``, ``compile``, ``run``) resolves to the *same*
  digest as the version pin, which is what catches a target whose push failed
  after some tags landed and left the floating tag on the previous release.

Targets are derived from the same sources ``containers/build.sh`` uses -- the
application list below and the ``containers/languages/*`` directories -- so a
newly added language is verified automatically.

Credentials are optional: public repositories verify anonymously. Set
``DOCKERHUB_USERNAME``/``DOCKERHUB_TOKEN`` and ``GITHUB_TOKEN`` to check private
ones.

Run with:

    uv run python scripts/verify_published_images.py --version v15.0.1
    uv run python scripts/verify_published_images.py --version v15.0.1 --registries ghcr
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import httpx

_APP_TARGETS = (
    "webapp",
    "arena",
    "autojudge",
    "rating",
    "aiassistant",
    "healthmonitor",
    "animator",
    "landingpage",
)
_LANGUAGES_DIR = Path(__file__).resolve().parents[1] / "containers" / "languages"
_TIMEOUT = httpx.Timeout(30.0)
_AUTH_FAILURE_CODES: Final = frozenset((httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN))
_MANIFEST_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)


@dataclass(frozen=True)
class Registry:
    """One registry's endpoints and naming convention."""

    name: str
    api: str
    auth_url: str
    auth_service: str
    separator: str
    credentials: tuple[str, str] | None

    def repository(self, namespace: str, prefix: str, component: str) -> str:
        """Build the repository path for one component.

        Args:
            namespace: The registry namespace (user or organization).
            prefix: The image family prefix, normally ``noca``.
            component: The image component, for example ``webapp``.

        Returns:
            The repository path used by this registry's naming convention.
        """
        return f"{namespace}/{prefix}{self.separator}{component}"


def dockerhub_registry() -> Registry:
    """Build the Docker Hub registry descriptor.

    Returns:
        The descriptor, carrying credentials when the environment supplies them.
    """
    user, token = os.environ.get("DOCKERHUB_USERNAME"), os.environ.get("DOCKERHUB_TOKEN")
    return Registry(
        name="Docker Hub",
        api="https://registry-1.docker.io",
        auth_url="https://auth.docker.io/token",
        auth_service="registry.docker.io",
        separator="-",
        credentials=(user, token) if user and token else None,
    )


def ghcr_registry() -> Registry:
    """Build the GHCR registry descriptor.

    Returns:
        The descriptor, carrying credentials when the environment supplies them.
    """
    username = os.environ.get("GHCR_USERNAME") or os.environ.get("GITHUB_ACTOR") or "token"
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or os.environ.get("GHCR_TOKEN")
    return Registry(
        name="GHCR",
        api="https://ghcr.io",
        auth_url="https://ghcr.io/token",
        auth_service="ghcr.io",
        separator="/",
        credentials=(username, token) if token else None,
    )


class ManifestProbeError(RuntimeError):
    """A registry response prevented the verifier from deciding tag state."""


class ManifestProbe:
    """Resolves manifest digests by tag, caching one pull token per repository."""

    def __init__(self, registry: Registry, transport: httpx.BaseTransport | None = None) -> None:
        """Prepare the probe.

        Args:
            registry: The registry to query.
            transport: Optional HTTP transport, primarily for isolated tests.
        """
        self._registry = registry
        self._client = httpx.Client(timeout=_TIMEOUT, transport=transport)
        self._tokens: dict[str, str] = {}

    def __enter__(self) -> ManifestProbe:
        """Enter the context manager."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def digest(self, repository: str, tag: str) -> str | None:
        """Resolve one tag to its manifest digest.

        Args:
            repository: The repository path.
            tag: The tag to resolve.

        Returns:
            The digest, or ``None`` only when the registry returns ``404``.

        Raises:
            ManifestProbeError: If authentication or the registry request fails.
        """
        token = self._token(repository)
        response = self._request(
            "HEAD",
            f"{self._registry.api}/v2/{repository}/manifests/{tag}",
            headers={"Accept": _MANIFEST_ACCEPT, "Authorization": f"Bearer {token}"},
        )
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        self._raise_for_probe_failure(response, "manifest request")
        digest: str | None = response.headers.get("Docker-Content-Digest")
        if not digest:
            raise ManifestProbeError("manifest response omitted Docker-Content-Digest")
        return digest

    def _token(self, repository: str) -> str:
        """Obtain (and cache) a pull token for one repository.

        Args:
            repository: The repository path.

        Returns:
            The bearer token.

        Raises:
            ManifestProbeError: If authentication or the token request fails.
        """
        if repository in self._tokens:
            return self._tokens[repository]
        response = self._request(
            "GET",
            self._registry.auth_url,
            params={
                "service": self._registry.auth_service,
                "scope": f"repository:{repository}:pull",
            },
            auth=self._registry.credentials,
        )
        self._raise_for_probe_failure(response, "token request")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ManifestProbeError("token response was not valid JSON") from exc
        issued = payload.get("token") or payload.get("access_token")
        if not issued:
            raise ManifestProbeError("token response omitted a bearer token")
        self._tokens[repository] = str(issued)
        return str(issued)

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        auth: tuple[str, str] | None = None,
    ) -> httpx.Response:
        """Send one registry request with a stable diagnostic on transport errors.

        Args:
            method: HTTP request method.
            url: Absolute request URL.
            headers: Optional request headers.
            params: Optional query parameters.
            auth: Optional basic-auth credentials.

        Returns:
            The registry response.

        Raises:
            ManifestProbeError: If the request cannot reach the registry.
        """
        try:
            return self._client.request(method, url, headers=headers, params=params, auth=auth)
        except httpx.HTTPError as exc:
            raise ManifestProbeError(f"registry request failed: {exc}") from exc

    @staticmethod
    def _raise_for_probe_failure(response: httpx.Response, operation: str) -> None:
        """Reject non-success responses without confusing access with absence.

        Args:
            response: Response to classify.
            operation: Human-readable operation name for diagnostics.

        Raises:
            ManifestProbeError: If the response is not successful.
        """
        if response.status_code == httpx.codes.OK:
            return
        if response.status_code in _AUTH_FAILURE_CODES:
            raise ManifestProbeError(f"authentication failed during {operation} (HTTP {response.status_code})")
        raise ManifestProbeError(f"{operation} failed with HTTP {response.status_code}")


def expected_images(
    version: str,
    include_apps: bool,
    include_languages: bool,
    components: tuple[str, ...] = (),
) -> list[tuple[str, str, str]]:
    """List every ``(component, floating_tag, versioned_tag)`` a release publishes.

    Args:
        version: The published version, for example ``v15.0.1``.
        include_apps: Whether to include the application images.
        include_languages: Whether to include the judge language images.
        components: When non-empty, keep only these components. A single-target
            publish verifies just what it built, so an unrelated image left on an
            older release is not reported as this run's failure.

    Returns:
        One entry per image slot that the release should have published.
    """
    images: list[tuple[str, str, str]] = []
    if include_apps:
        images += [(app, "latest", version) for app in _APP_TARGETS]
    if include_languages:
        languages = sorted(path.name for path in _LANGUAGES_DIR.iterdir() if path.is_dir())
        for language in languages:
            images += [(f"judge-{language}", slot, f"{slot}-{version}") for slot in ("compile", "run")]
    if components:
        images = [image for image in images if image[0] in components]
    return images


def verify(registry: Registry, namespace: str, prefix: str, images: list[tuple[str, str, str]]) -> list[str]:
    """Check every expected tag of one registry.

    Args:
        registry: The registry to verify.
        namespace: The registry namespace.
        prefix: The image family prefix.
        images: The expected ``(component, floating, versioned)`` entries.

    Returns:
        Human-readable problem descriptions, empty when the release is complete.
    """
    problems: list[str] = []
    with ManifestProbe(registry) as probe:

        def check(entry: tuple[str, str, str]) -> str | None:
            component, floating, versioned = entry
            repository = registry.repository(namespace, prefix, component)
            try:
                pinned_digest = probe.digest(repository, versioned)
            except ManifestProbeError as exc:
                return f"{component}: could not verify {versioned}: {exc}"
            if pinned_digest is None:
                return f"{component}: {versioned} is missing"
            try:
                floating_digest = probe.digest(repository, floating)
            except ManifestProbeError as exc:
                return f"{component}: could not verify {floating}: {exc}"
            if floating_digest != pinned_digest:
                return f"{component}: {floating} does not point at {versioned}"
            return None

        with futures.ThreadPoolExecutor(max_workers=12) as pool:
            problems = [problem for problem in pool.map(check, images) if problem]
    return sorted(problems)


def main(argv: list[str] | None = None) -> int:
    """Verify a published release.

    Args:
        argv: Command line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        ``0`` when every expected tag is present, ``1`` otherwise.
    """
    parser = argparse.ArgumentParser(
        prog="verify_published_images",
        description="Check that a release published every expected image tag.",
    )
    parser.add_argument("--version", required=True, help="Published version, for example v15.0.1.")
    parser.add_argument(
        "--registries",
        choices=("both", "dockerhub", "ghcr"),
        default="both",
        help="Which registries to verify (default: both).",
    )
    parser.add_argument(
        "--scope",
        choices=("all", "apps", "languages"),
        default="all",
        help="Which image family to verify (default: all).",
    )
    parser.add_argument(
        "--component",
        action="append",
        default=[],
        dest="components",
        metavar="NAME",
        help=(
            "Verify only this component (repeatable), for example webapp or judge-python3. "
            "Defaults to every component in the selected scope."
        ),
    )
    parser.add_argument("--dockerhub-namespace", default="dclobato", help="Docker Hub namespace.")
    parser.add_argument("--ghcr-owner", default="dclobato", help="GHCR package owner.")
    parser.add_argument("--prefix", default="noca", help="Image family prefix (default: noca).")
    args = parser.parse_args(argv)

    images = expected_images(
        args.version,
        include_apps=args.scope in ("all", "apps"),
        include_languages=args.scope in ("all", "languages"),
        components=tuple(args.components),
    )
    if not images:
        print("No image slots match the requested scope and components.")
        return 1

    targets: list[tuple[Registry, str]] = []
    if args.registries in ("both", "dockerhub"):
        targets.append((dockerhub_registry(), args.dockerhub_namespace))
    if args.registries in ("both", "ghcr"):
        targets.append((ghcr_registry(), args.ghcr_owner))

    failed = False
    for registry, namespace in targets:
        problems = verify(registry, namespace, args.prefix, images)
        print(f"== {registry.name}: {len(images)} image slots expected for {args.version}")
        for problem in problems:
            print(f"    ! {problem}")
        if problems:
            failed = True
            print(f"   {len(problems)} problem(s) found")
        else:
            print("   complete")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
