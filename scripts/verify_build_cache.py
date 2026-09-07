#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Verify that a publish run exported a registry cache entry for every target.

``containers/build.sh`` gives each Bake target its own tag in the cache
repository (``docker.io/dclobato/noca-buildcache`` and
``ghcr.io/dclobato/noca/buildcache``), so a publish should leave one cache tag
per target plus the four internal bases.

A *cache export* failure is not a build failure. BuildKit reports it as a
warning, the push still succeeds, ``build.sh`` still exits ``0``, and the
publish workflow's retry loop -- which only reacts to a non-zero exit -- never
fires. ``verify_published_images.py`` does not catch it either: it checks the
published image tags, which are all present. The result is silent, and it has
already happened: after ``v19.0.0`` the Docker Hub cache held 31 of the 42
language targets, so 11 of them (all of ``c-sharp``, plus one slot each of
``go``, ``haskell``, ``java``, ``kotlin``, ``lua``, ``php``, ``ruby``, ``scala``
and ``swift``) rebuilt from scratch on every subsequent run.

This script asks each registry whether every expected cache tag resolves.
Existence is the whole check: cache tags are floating and carry no version, and
a cache entry left over from an earlier release is still a useful partial hit,
so only a *missing* entry is reported.

A missing entry costs build time, never correctness -- the release itself is
fine. Publish workflows should therefore run this with
``continue-on-error: true``, so the step reports the gap without failing a
release that actually published everything.

Targets are derived from the same sources ``containers/build.sh`` uses, so a
newly added language is covered automatically.

Credentials are optional: public repositories verify anonymously. Set
``DOCKERHUB_USERNAME``/``DOCKERHUB_TOKEN`` and ``GITHUB_TOKEN`` to check private
ones -- the GHCR cache repository is private, so it always needs a token.

Run with:

    uv run python scripts/verify_build_cache.py
    uv run python scripts/verify_build_cache.py --registries ghcr --scope languages
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.verify_published_images import (  # noqa: E402
    APP_TARGETS,
    LANGUAGES_DIR,
    ManifestProbe,
    ManifestProbeError,
    Registry,
    dockerhub_registry,
    ghcr_registry,
)

#: Targets ``build.sh`` always adds to the cache set, whatever was requested.
#: They are ``type=cacheonly`` dependencies rather than publishable images, so
#: they appear in no image listing and must be named here. Mirrors the list in
#: ``containers/build.sh`` (the ``for cache_target in ...`` loop).
INTERNAL_BASES = ("app-base", "assets-base", "isolate-base", "judge-compile-base")


def expected_cache_tags(
    include_apps: bool,
    include_languages: bool,
    components: tuple[str, ...] = (),
) -> list[str]:
    """List every cache tag a publish run should have written.

    The internal bases are included in every scope because ``build.sh`` adds
    them to the cache set unconditionally -- an apps-only publish still exports
    ``isolate-base`` and ``judge-compile-base``.

    Args:
        include_apps: Whether to include the application targets.
        include_languages: Whether to include the judge language targets.
        components: When non-empty, keep only these cache tags, plus the
            internal bases. A single-target publish writes just its own entry,
            so an unrelated target's cache is not this run's responsibility.

    Returns:
        The expected cache tag names, sorted and without duplicates.
    """
    tags: set[str] = set(INTERNAL_BASES)
    selected: set[str] = set()
    if include_apps:
        selected |= set(APP_TARGETS)
    if include_languages:
        languages = sorted(path.name for path in LANGUAGES_DIR.iterdir() if path.is_dir())
        for language in languages:
            for slot in ("compile", "run"):
                if (LANGUAGES_DIR / language / slot).is_dir():
                    selected.add(f"judge-{language}-{slot}")
    if components:
        selected &= set(components)
    return sorted(tags | selected)


def verify(
    registry: Registry,
    namespace: str,
    prefix: str,
    repository: str,
    tags: list[str],
    transport: httpx.BaseTransport | None = None,
) -> list[str]:
    """Check that every expected cache tag resolves in one registry.

    Args:
        registry: The registry to verify.
        namespace: The registry namespace.
        prefix: The image family prefix, normally ``noca``.
        repository: The cache repository component, normally ``buildcache``.
        tags: The expected cache tag names.
        transport: Optional HTTP transport, primarily for isolated tests.

    Returns:
        Human-readable problem descriptions, empty when the cache is complete.
    """
    cache_repository = registry.repository(namespace, prefix, repository)
    with ManifestProbe(registry, transport) as probe:

        def check(tag: str) -> str | None:
            try:
                digest = probe.digest(cache_repository, tag)
            except ManifestProbeError as exc:
                return f"{tag}: could not verify: {exc}"
            if digest is None:
                return f"{tag}: no cache entry (this target rebuilds from scratch)"
            return None

        with futures.ThreadPoolExecutor(max_workers=12) as pool:
            problems = [problem for problem in pool.map(check, tags) if problem]
    return sorted(problems)


def main(argv: list[str] | None = None) -> int:
    """Verify the registry build cache.

    Args:
        argv: Command line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        ``0`` when every expected cache tag is present, ``1`` otherwise.
    """
    parser = argparse.ArgumentParser(
        prog="verify_build_cache",
        description="Check that a publish exported a cache entry for every Bake target.",
    )
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
        help="Which target family to verify (default: all).",
    )
    parser.add_argument(
        "--component",
        action="append",
        default=[],
        dest="components",
        metavar="NAME",
        help=(
            "Verify only this cache tag (repeatable), for example webapp or "
            "judge-python3-compile. The internal bases are always verified."
        ),
    )
    parser.add_argument("--dockerhub-namespace", default="dclobato", help="Docker Hub namespace.")
    parser.add_argument("--ghcr-owner", default="dclobato", help="GHCR package owner.")
    parser.add_argument("--prefix", default="noca", help="Image family prefix (default: noca).")
    parser.add_argument(
        "--cache-repository",
        default="buildcache",
        help="Cache repository component appended to the prefix (default: buildcache).",
    )
    args = parser.parse_args(argv)

    tags = expected_cache_tags(
        include_apps=args.scope in ("all", "apps"),
        include_languages=args.scope in ("all", "languages"),
        components=tuple(args.components),
    )

    targets: list[tuple[Registry, str]] = []
    if args.registries in ("both", "dockerhub"):
        targets.append((dockerhub_registry(), args.dockerhub_namespace))
    if args.registries in ("both", "ghcr"):
        targets.append((ghcr_registry(), args.ghcr_owner))

    failed = False
    for registry, namespace in targets:
        problems = verify(registry, namespace, args.prefix, args.cache_repository, tags)
        print(f"== {registry.name}: {len(tags)} cache entries expected")
        for problem in problems:
            print(f"    ! {problem}")
        if problems:
            failed = True
            print(f"   {len(problems)} of {len(tags)} missing — those targets build cold")
        else:
            print("   complete")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
