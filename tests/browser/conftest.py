#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Fixtures for the browser UI checks.

These drive a **running development server** with a real login, because the
defects they exist to catch cannot fail anywhere else: a control that renders but
has no listener bound, an editor that paints blank because it was constructed
inside a hidden tab pane. Both shipped past a full green suite.

Everything here is skipped unless ``NOCA_UI_CHECK_USERNAME`` and
``NOCA_UI_CHECK_PASSWORD`` are set, so an ordinary ``uv run pytest`` is
unaffected and CI needs no browser.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is a dev dependency")

# The credentials live in `.env` beside the rest of the development configuration,
# so read that file here: the wider test suite talks to os.environ directly and
# never loads it. Real environment variables still win, so CI can override.
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
if _ENV_FILE.is_file():
    from dotenv import load_dotenv

    load_dotenv(_ENV_FILE, override=False)

from playwright.sync_api import Browser, ConsoleMessage, Page, sync_playwright  # noqa: E402

ARENA_URL = os.environ.get("NOCA_UI_CHECK_ARENA_URL", "http://127.0.0.1:8001").rstrip("/")
WEB_URL = os.environ.get("NOCA_UI_CHECK_WEB_URL", "http://127.0.0.1:8000").rstrip("/")
WEB_SLUG = os.environ.get("NOCA_UI_CHECK_WEB_SLUG", "")
USERNAME = os.environ.get("NOCA_UI_CHECK_USERNAME", "")
PASSWORD = os.environ.get("NOCA_UI_CHECK_PASSWORD", "")

# Web and Arena are separate identity domains, and the Contest checks sign in as
# an UberAdmin -- which is rarely the same account as an Arena login. So the Web
# credentials are their own pair, falling back to the base pair for the case
# where one account genuinely exists in both.
WEB_USERNAME = os.environ.get("NOCA_UI_CHECK_WEB_USERNAME", "") or USERNAME
WEB_PASSWORD = os.environ.get("NOCA_UI_CHECK_WEB_PASSWORD", "") or PASSWORD

# Playwright's *sync* API refuses to run inside a running asyncio loop, and under
# pytest-xdist a worker that has already run an asyncio test carries one. Mixing
# these checks into a parallel run therefore breaks that worker's async tests
# rather than just skipping -- so they are skipped under xdist and run explicitly:
#
#     uv run pytest tests/browser
_UNDER_XDIST = bool(os.environ.get("PYTEST_XDIST_WORKER"))

requires_credentials = pytest.mark.skipif(
    _UNDER_XDIST or not (USERNAME and PASSWORD),
    reason=(
        "run the browser checks on their own (`uv run pytest tests/browser`) with "
        "NOCA_UI_CHECK_USERNAME and NOCA_UI_CHECK_PASSWORD set; the sync Playwright "
        "API cannot share an xdist worker with asyncio tests"
    ),
)
requires_web_slug = pytest.mark.skipif(
    not (WEB_SLUG and WEB_USERNAME and WEB_PASSWORD),
    reason=(
        "set NOCA_UI_CHECK_WEB_SLUG (and NOCA_UI_CHECK_WEB_USERNAME / "
        "NOCA_UI_CHECK_WEB_PASSWORD when the UberAdmin differs from the Arena login) "
        "to run the Contest admin browser checks"
    ),
)


def _assert_signed_in(page: Page, login_path: str, who: str) -> None:
    """Fail with a readable message when authentication did not take.

    Without this a wrong credential surfaces as a selector timeout 30 seconds
    later, pointing at whatever element the test wanted next rather than at the
    login that never happened.
    """
    if login_path in page.url:
        raise AssertionError(
            f"{who} login was rejected -- still on {page.url}. "
            "Check the credentials in .env for this module's identity domain."
        )


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    """One headless Chromium for the whole session.

    ``--no-sandbox`` is required because Ubuntu 24.04 restricts unprivileged user
    namespaces, which Chromium's sandbox needs.
    """
    with sync_playwright() as play:
        instance = play.chromium.launch(args=["--no-sandbox"])
        yield instance
        instance.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    """A fresh page that fails the test on any browser console error.

    A silent JavaScript exception is exactly how a button comes to render and do
    nothing, so it is treated as a failure rather than left for a human to notice.
    """
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    active = context.new_page()
    errors: list[str] = []

    def record(message: ConsoleMessage) -> None:
        if message.type == "error":
            errors.append(message.text)

    active.on("console", record)
    active.on("pageerror", lambda exc: errors.append(str(exc)))

    yield active

    context.close()
    assert not errors, "browser console errors: " + " | ".join(errors)


def arena_login(page: Page) -> None:
    """Authenticate against the running Arena instance."""
    page.goto(f"{ARENA_URL}/auth/login", wait_until="domcontentloaded")
    page.fill('input[name="email"]', USERNAME)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state("domcontentloaded")
    _assert_signed_in(page, "/auth/login", "Arena")


def first_arena_problem_edit_url(page: Page) -> str:
    """Return an existing problem's edit URL, or skip.

    The inline add-row controls live on the *edit* editor: Arena creation does not
    accept test cases yet, so its Test cases pane is a read-only empty state until
    the problem exists. Discovering a problem from the list keeps these checks
    read-only -- they never create or modify data on the target instance.
    """
    page.goto(f"{ARENA_URL}/admin/problems", wait_until="domcontentloaded")
    links = page.eval_on_selector_all('a[href*="/edit"]', 'els => els.map(e => e.getAttribute("href"))')
    if not links:
        pytest.skip("the target Arena instance has no problem to open")
    return str(links[0])


def web_login(page: Page) -> None:
    """Authenticate against the running Web instance as an UberAdmin.

    ``/login`` is the UberAdmin login; an UberAdmin administers any contest, so
    the checks need no per-contest membership. ``/c/{slug}/login`` is the separate
    contest-user login and is not used here.
    """
    page.goto(f"{WEB_URL}/login", wait_until="domcontentloaded")
    page.fill('input[name="identifier"]', WEB_USERNAME)
    page.fill('input[name="password"]', WEB_PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state("domcontentloaded")
    _assert_signed_in(page, "/login", "Contest UberAdmin")
