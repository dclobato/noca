#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for registry build-cache verification."""

from __future__ import annotations

import httpx

from scripts.verify_build_cache import INTERNAL_BASES, expected_cache_tags, verify
from scripts.verify_published_images import APP_TARGETS, LANGUAGES_DIR, Registry

_DIGEST = f"sha256:{'b' * 64}"


def _registry() -> Registry:
    """Build an isolated registry descriptor.

    Returns:
        A descriptor pointing at the mocked host, using flat naming.
    """
    return Registry(
        name="Test registry",
        api="https://registry.example.test",
        auth_url="https://registry.example.test/token",
        auth_service="registry.example.test",
        separator="-",
        credentials=None,
    )


def _transport(missing: set[str]) -> httpx.MockTransport:
    """Build a registry transport where selected tags are absent.

    Args:
        missing: Tag names the registry should answer ``404`` for.

    Returns:
        A deterministic mock transport.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"token": "pull-token"})
        if request.url.path.rsplit("/", 1)[-1] in missing:
            return httpx.Response(404)
        return httpx.Response(200, headers={"Docker-Content-Digest": _DIGEST})

    return httpx.MockTransport(handler)


def test_every_language_slot_and_internal_base_is_expected() -> None:
    """The full scope covers both slots of every language plus the bases."""
    tags = expected_cache_tags(include_apps=True, include_languages=True)
    languages = sorted(path.name for path in LANGUAGES_DIR.iterdir() if path.is_dir())

    assert set(INTERNAL_BASES) <= set(tags)
    assert set(APP_TARGETS) <= set(tags)
    for language in languages:
        assert f"judge-{language}-compile" in tags
        assert f"judge-{language}-run" in tags
    assert len(tags) == len(APP_TARGETS) + 2 * len(languages) + len(INTERNAL_BASES)


def test_apps_scope_still_expects_the_judge_bases() -> None:
    """``build.sh`` exports the four bases whatever targets were requested."""
    tags = expected_cache_tags(include_apps=True, include_languages=False)

    assert set(tags) == set(APP_TARGETS) | set(INTERNAL_BASES)
    assert "judge-compile-base" in tags
    assert not any(tag.startswith("judge-") and tag != "judge-compile-base" for tag in tags)


def test_component_filter_keeps_the_bases_only() -> None:
    """A single-target publish is answerable for its own entry and the bases."""
    tags = expected_cache_tags(include_apps=True, include_languages=True, components=("webapp",))

    assert set(tags) == {"webapp"} | set(INTERNAL_BASES)


def test_verify_reports_a_missing_cache_entry() -> None:
    """A ``404`` on one tag is reported and the others stay silent."""
    problems = verify(
        _registry(),
        "dclobato",
        "noca",
        "buildcache",
        ["webapp", "judge-c-sharp-compile"],
        _transport({"judge-c-sharp-compile"}),
    )

    assert len(problems) == 1
    assert problems[0].startswith("judge-c-sharp-compile: no cache entry")


def test_verify_is_silent_when_every_entry_resolves() -> None:
    """A complete cache produces no problems."""
    problems = verify(
        _registry(),
        "dclobato",
        "noca",
        "buildcache",
        ["webapp", "app-base"],
        _transport(set()),
    )

    assert problems == []
