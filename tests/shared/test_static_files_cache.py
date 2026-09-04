#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Guard: a deploy is never half-applied by a browser's CSS cache.

Only the top-level stylesheet carries `?v={app_version}`; the design system
under it arrives through un-versioned `@import` URLs. If those responses may be
reused without asking, a freshly deployed page renders against the previous
release's CSS -- which is what it looks like when rendered-Markdown table
borders and heading gaps vanish after an upgrade.

These tests exercise the ASGI app rather than the class in isolation, because
the behaviour that matters is what a browser receives: the directive on the
first response, and a `304` on the revalidation it forces.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette

from shared.static_files import REVALIDATE_CACHE_CONTROL, RevalidatedStaticFiles

_SHARED_CSS = Path(__file__).resolve().parents[2] / "shared" / "static" / "css"
_SHARED_IMG = Path(__file__).resolve().parents[2] / "shared" / "static" / "img"


def _app() -> Starlette:
    """Return an app mounting the shared CSS exactly as every module does."""
    app = Starlette()
    app.mount("/static/shared-css", RevalidatedStaticFiles(directory=_SHARED_CSS), name="static_shared_css")
    return app


@pytest.mark.asyncio
async def test_shared_css_must_be_revalidated() -> None:
    """`common.css` owns the rendered-Markdown rules and is never versioned."""
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/static/shared-css/common.css")

    assert response.status_code == 200
    assert response.headers["cache-control"] == REVALIDATE_CACHE_CONTROL
    # Revalidation is only cheap because there is something to revalidate with.
    assert response.headers.get("etag")


@pytest.mark.asyncio
async def test_revalidation_of_an_unchanged_file_costs_no_body() -> None:
    """The forced conditional request answers 304, not a second transfer."""
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/static/shared-css/common.css")
        second = await client.get(
            "/static/shared-css/common.css",
            headers={"if-none-match": first.headers["etag"]},
        )

    assert second.status_code == 304
    assert second.content == b""
    # The 304 restates the rule, so a client cannot infer a weaker one from it.
    assert second.headers["cache-control"] == REVALIDATE_CACHE_CONTROL


@pytest.mark.asyncio
async def test_every_module_mounts_static_css_and_js_this_way() -> None:
    """A module that mounts plain `StaticFiles` reopens the stale window."""
    from starlette.staticfiles import StaticFiles

    import animator.main
    import arena.main
    import healthmonitor.main
    import web.main

    for module in (web.main, arena.main, animator.main, healthmonitor.main):
        for route in module.app.routes:
            mounted = getattr(route, "app", None)
            if not isinstance(mounted, StaticFiles):
                continue
            directory = str(getattr(mounted, "directory", ""))
            if not directory.endswith(("/css", "/js")):
                continue
            assert isinstance(mounted, RevalidatedStaticFiles), (
                f"{module.__name__} mounts {directory} without revalidation"
            )


def test_arena_and_web_mount_shared_images() -> None:
    """Arena and Web expose shared artwork through the same named mount."""
    from starlette.staticfiles import StaticFiles

    import arena.main
    import web.main

    for module in (arena.main, web.main):
        path = module.app.url_path_for("static_shared_img", path="throttled.webp")
        route = next(route for route in module.app.routes if route.name == "static_shared_img")

        assert str(path) == "/static/shared-img/throttled.webp"
        assert isinstance(route.app, StaticFiles)
        assert route.app.directory is not None
        assert Path(route.app.directory).resolve() == _SHARED_IMG.resolve()
        assert (_SHARED_IMG / "throttled.webp").is_file()
