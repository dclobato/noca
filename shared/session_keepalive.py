#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The arithmetic behind sliding sessions, shared by Web and Arena.

Both modules rotate an auth cookie once a request arrives inside the tail of its
token's life, and both have a browser-side heartbeat whose only job is to be that
request for a page left open. Three quantities are involved -- the window, the
derived cadence, and the floor under it -- and every one of them was written out
twice, once per module.

That duplication was not merely untidy. A configuration validator has to reject a
cadence that lands *outside* the window, so the validator and the middleware must
agree on where the window starts. Two copies of `lifetime // 2` are two chances to
disagree, and a disagreement would be silent: the validator would accept a
configuration whose heartbeat rotates nothing, which is precisely the expiry the
heartbeat exists to prevent.
"""

from __future__ import annotations

#: Smallest cadence any keepalive may use, whatever the token lifetime. It bounds
#: request volume; a lifetime short enough for this floor to exceed the refresh
#: window is rejected at startup rather than clamped.
KEEPALIVE_MIN_INTERVAL_SECONDS = 60

#: A derived cadence is this fraction of the token lifetime, giving two pings of
#: margin inside the half-life window.
_KEEPALIVE_LIFETIME_DIVISOR = 4


def refresh_window_seconds(token_lifetime_seconds: int) -> int:
    """Return the remaining lifetime at which a valid token starts being rotated.

    Args:
        token_lifetime_seconds: Configured lifetime of a freshly issued token.

    Returns:
        int: Seconds-remaining threshold; a request arriving at or below it
        rotates the cookie. Never zero, so a pathologically short lifetime still
        has a window rather than none at all.
    """
    return max(1, token_lifetime_seconds // 2)


def derived_keepalive_seconds(token_lifetime_seconds: int) -> int:
    """Return the browser keepalive cadence derived from a token lifetime.

    Derived rather than configured on purpose: a cadence knob could be set past
    the refresh window, where the ping rotates nothing and silently restores the
    mid-edit expiry it exists to prevent.

    Args:
        token_lifetime_seconds: Configured lifetime of a freshly issued token.

    Returns:
        int: Interval in seconds for the client-side heartbeat timer.
    """
    return max(KEEPALIVE_MIN_INTERVAL_SECONDS, token_lifetime_seconds // _KEEPALIVE_LIFETIME_DIVISOR)


def keepalive_lands_inside_refresh_window(
    *,
    keepalive_seconds: int,
    token_lifetime_seconds: int,
) -> bool:
    """Return whether a keepalive cadence actually rotates the cookie.

    A ping slower than the window can pass through the whole window between two
    beats and leave the token to expire under an open page.

    Args:
        keepalive_seconds: Effective cadence the browser will use.
        token_lifetime_seconds: Configured lifetime of a freshly issued token.

    Returns:
        bool: Whether every ping is guaranteed to arrive inside the window.
    """
    return keepalive_seconds < refresh_window_seconds(token_lifetime_seconds)


def keepalive_window_error(
    *,
    keepalive_seconds: int,
    token_lifetime_seconds: int,
    cadence_setting: str | None,
    lifetime_setting: str = "NOCA_JWT_EXPIRE_SECONDS",
) -> str:
    """Return the operator-facing message for a cadence outside the window.

    Args:
        keepalive_seconds: Effective cadence the browser would use.
        token_lifetime_seconds: Configured lifetime of a freshly issued token.
        cadence_setting: Name of the setting that produced the cadence, or
            ``None`` when the cadence is derived and cannot be lowered directly.
        lifetime_setting: Name of the token-lifetime setting.

    Returns:
        str: A message naming the window, and a remedy the operator can act on --
        which for a derived cadence is only the lifetime, since there is no
        cadence setting to lower.
    """
    subject = cadence_setting or "The derived session keepalive"
    remedy = f"Raise {lifetime_setting}"
    if cadence_setting is not None:
        remedy = f"Lower {cadence_setting} or raise {lifetime_setting}"
    return (
        f"{subject} would ping every {keepalive_seconds}s, but "
        f"{lifetime_setting}={token_lifetime_seconds} rotates a session only in its "
        f"last {refresh_window_seconds(token_lifetime_seconds)}s. The heartbeat would "
        f"rotate nothing and open pages would be logged out mid-edit. {remedy}."
    )


__all__ = [
    "KEEPALIVE_MIN_INTERVAL_SECONDS",
    "derived_keepalive_seconds",
    "keepalive_lands_inside_refresh_window",
    "keepalive_window_error",
    "refresh_window_seconds",
]
