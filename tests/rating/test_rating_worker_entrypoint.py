#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the rating worker process entry point."""

import shlex
import sys
from types import ModuleType
from typing import Any

import pytest

from rating import worker
from rating.config import settings
from shared.enumerations import Environment


def test_development_main_uses_command_reloader_and_suppresses_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run the development worker without multiprocessing shutdown tracebacks."""
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []
    watchfiles = ModuleType("watchfiles")

    def fake_run_process(*paths: str, **kwargs: Any) -> int:
        """Record the reload configuration and emulate an interrupted join."""
        calls.append((paths, kwargs))
        raise KeyboardInterrupt

    watchfiles.run_process = fake_run_process  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "watchfiles", watchfiles)
    monkeypatch.setattr(settings, "ENVIRONMENT", Environment.DEVELOPMENT)

    worker.main()

    assert calls == [
        (
            ("rating", "shared"),
            {
                "target": shlex.join(
                    [
                        sys.executable,
                        "-c",
                        "from rating.worker import _run_rating_process; _run_rating_process()",
                    ]
                ),
                "target_type": "command",
            },
        )
    ]
