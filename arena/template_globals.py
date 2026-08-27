#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The single definition of what Arena templates may read.

``_base.html`` and its partials resolve a fixed set of names -- helpers, brand
and version strings, upload limits, verdict labels. This module is where that
set is declared, once, so the production application and every test application
render against the same contract.

It used to live inline in ``arena/main.py``, which meant a test application had
to re-declare whichever subset its page happened to touch. Those subsets drifted
apart (27 of them set ``app_version``, 25 ``next_rating_update_text``, 3
``token_expiry_text``, 1 ``brand_name``), and the templates papered over the gaps
with ``is defined`` guards -- which then hid a real omission just as readily as a
test-only one.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Request
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash

from arena.config import settings
from arena.image_upload_limits import ARENA_LOGO_MAX_FILE_SIZE
from arena.services.admin_user_service import ARENA_ROLE_DISPLAY
from arena.services.ranking_medals import arena_medal_band
from arena.services.session_service import get_session_started_at
from arena.services.user_timezone_service import (
    datetime_local_value,
    format_relative_datetime,
    format_user_datetime,
    timezone_name_for_user,
)
from shared.enumerations import VERDICT_BADGE_CLASSES, VERDICT_LABELS
from shared.services.arena_rating import format_next_rating_update
from shared.services.problem_image import (
    MAX_PROBLEM_IMAGE_BYTES,
    MAX_PROBLEM_IMAGE_HEIGHT,
    MAX_PROBLEM_IMAGE_WIDTH,
)
from shared.tc_zip import MAX_INLINE_TESTCASE_BYTES
from shared.timing import format_compact_duration


def next_rating_update_text(request: Request) -> str | None:
    """Return the footer-ready next rating update text for a request.

    Args:
        request: Current FastAPI request whose app state stores scheduler data.

    Returns:
        Relative duration text, or ``None`` when the scheduler has no active deadline.
    """
    return format_next_rating_update(getattr(request.app.state, "next_rating_update", None))


def arena_online_user_count(request: Request) -> int | None:
    """Return the cached count of online Arena users for footer rendering.

    Reads the value refreshed by ``_online_users_count_poller`` so the
    synchronous template helper needs no per-request Valkey access. Returns
    ``None`` when presence is disabled or the poller has not produced a value.

    Args:
        request: Current FastAPI request whose app state stores the count.

    Returns:
        The online-user count, or ``None`` when unavailable.
    """
    return getattr(request.app.state, "arena_online_user_count", None)


def session_heartbeat_seconds() -> int:
    """Return the client heartbeat interval, in seconds.

    The cadence itself is `arena.config.Settings.session_keepalive_seconds`, so
    the value rendered into the page is the same one the startup validator
    checked against the token refresh window.

    Returns:
        int: Interval in seconds for the client-side heartbeat timer.
    """
    return settings.session_keepalive_seconds


def heartbeat_config(request: Request) -> dict[str, object]:
    """Return the client heartbeat configuration for ``_base.html``.

    Args:
        request: Current FastAPI request, used to resolve the endpoint URLs.

    Returns:
        The heartbeat URLs, interval, and presence flag.

    Raises:
        NoMatchFound: When the presence routes are not registered on the running
            application. This is deliberately loud: the heartbeat is what keeps a
            sliding session alive on a page left open, so an application missing
            those routes must fail rather than quietly render without it.
    """
    return {
        "heartbeat_url": str(request.url_for("arena_presence_heartbeat")),
        "status_url": str(request.url_for("arena_presence_status")),
        "interval_seconds": session_heartbeat_seconds(),
        "presence_enabled": settings.PRESENCE_ENABLED,
    }


def token_expiry_text(request: Request) -> str | None:
    """Return a human-readable description of how long the current session is still valid.

    Every session slides: the middleware silently rotates the LOGIN token at
    half-life for as long as the user keeps making requests, so the token's own
    ``expires_in`` is not a session lifetime and must never be shown as one.

    What can end an active session is the optional absolute cap
    ``NOCA_JWT_REFRESH_MAX_SESSION_SECONDS``, so that deadline
    (``session_started_at + cap``) is what this reports. With the cap disabled
    (``0``, the default) an active session has no expiry to announce and this
    returns ``None``, which hides the footer indicator.

    Args:
        request: Current FastAPI request whose state carries the validated token.

    Returns:
        A human-readable duration string (e.g. ``"29d 23h"``, ``"2h 30m"``, ``"< 1 min"``),
        or ``None`` when no authenticated session is active or no cap is configured.
    """
    validation = getattr(request.state, "validated_token", None)
    if validation is None:
        return None

    max_session_seconds = settings.JWT_REFRESH_MAX_SESSION_SECONDS
    if max_session_seconds <= 0:
        return None

    session_started_at = get_session_started_at(validation)
    if session_started_at is None:
        return None

    remaining = session_started_at + max_session_seconds - int(datetime.now(UTC).timestamp())
    if remaining <= 0:
        return None

    days, rem = divmod(int(remaining), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days > 0:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours > 0:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    if minutes > 0:
        return f"{minutes}m"
    return "< 1 min"


def fmt_shell_cmd(cmd: list[str] | None) -> str:
    """Join a command list and split on ``&&`` for readable display.

    Args:
        cmd: Command argument list, or ``None``.

    Returns:
        str: Display-ready command string.
    """
    if not cmd:
        return ""
    return " && \\\n".join(" ".join(cmd).split(" && "))


def register_arena_template_globals(templates: Jinja2Templates, *, app_version: str) -> None:
    """Install every global and filter Arena templates are allowed to read.

    Args:
        templates: Jinja2 templates object to configure in place.
        app_version: Version string rendered in the footer and asset cache keys.
    """
    globals_ = templates.env.globals
    globals_["MAX_INLINE_TESTCASE_BYTES"] = MAX_INLINE_TESTCASE_BYTES
    globals_["app_version"] = app_version
    globals_["brand_name"] = settings.BRAND_NAME
    globals_["healthmon_url"] = settings.HEALTHMON_URL
    globals_["arena_datetime_local_value"] = datetime_local_value
    globals_["arena_format_datetime"] = format_user_datetime
    globals_["arena_format_relative_datetime"] = format_relative_datetime
    globals_["format_compact_duration"] = format_compact_duration
    globals_["arena_user_timezone_name"] = timezone_name_for_user
    globals_["arena_medal_band"] = arena_medal_band
    globals_["arena_role_labels"] = ARENA_ROLE_DISPLAY
    globals_["verdict_badge_classes"] = VERDICT_BADGE_CLASSES
    globals_["verdict_labels"] = VERDICT_LABELS
    globals_["image_max_file_size_mib"] = settings.IMAGE_MAX_FILE_SIZE / (1024 * 1024)
    globals_["image_max_width"] = settings.IMAGE_MAX_WIDTH
    globals_["image_max_height"] = settings.IMAGE_MAX_HEIGHT
    globals_["affiliation_logo_max_file_size_mib"] = ARENA_LOGO_MAX_FILE_SIZE / (1024 * 1024)
    globals_["problem_image_max_file_size_mib"] = MAX_PROBLEM_IMAGE_BYTES / (1024 * 1024)
    globals_["problem_image_max_width"] = MAX_PROBLEM_IMAGE_WIDTH
    globals_["problem_image_max_height"] = MAX_PROBLEM_IMAGE_HEIGHT
    globals_["heartbeat_config"] = heartbeat_config
    globals_["arena_online_user_count"] = arena_online_user_count
    globals_["next_rating_update_text"] = next_rating_update_text
    globals_["token_expiry_text"] = token_expiry_text
    templates.env.filters["fmt_shell_cmd"] = fmt_shell_cmd
    setup_flash(templates)


__all__ = [
    "arena_online_user_count",
    "fmt_shell_cmd",
    "heartbeat_config",
    "next_rating_update_text",
    "register_arena_template_globals",
    "session_heartbeat_seconds",
    "token_expiry_text",
]
