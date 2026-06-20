#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Container healthcheck entrypoint for the web service."""

from __future__ import annotations

import json
import urllib.request


def healthcheck_is_healthy() -> bool:
    """Return whether the local web health endpoint reports a healthy state.

    Returns:
        ``True`` when the local ``/health`` endpoint responds with HTTP 200 and
        a status of ``ok``; otherwise ``False``.
    """
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5) as response:
            payload = json.load(response)
            status = payload.get("status")
            return response.status == 200 and isinstance(status, str) and status == "ok"
    except Exception:
        return False


def main() -> int:
    """Run the container healthcheck process.

    Returns:
        Process exit code expected by Docker healthchecks.
    """
    return 0 if healthcheck_is_healthy() else 1


if __name__ == "__main__":
    raise SystemExit(main())
