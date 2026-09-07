#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The four ``NOCA_WEB_*_RATE_LIMIT_*`` export and report budgets: defaults, aliases, bounds."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.web.test_web_config_public_rate_limits import _make_settings

#: (settings prefix, env prefix, default max requests)
_BUDGETS = (
    ("ADMIN_EXPORT", "NOCA_WEB_ADMIN_EXPORT", 20),
    ("CONTEST_REPORT", "NOCA_WEB_CONTEST_REPORT", 60),
    ("TEAM_DOWNLOAD", "NOCA_WEB_TEAM_DOWNLOAD", 5),
    ("UBERADMIN_EXPORT", "NOCA_WEB_UBERADMIN_EXPORT", 10),
)


@pytest.mark.parametrize(("field", "env", "default_max"), _BUDGETS, ids=[b[0] for b in _BUDGETS])
def test_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str, env: str, default_max: int) -> None:
    """Enabled, ten-minute windows, and a per-surface ceiling sized to honest usage."""
    settings = _make_settings(monkeypatch, tmp_path)
    assert getattr(settings, f"{field}_RATE_LIMIT_ENABLED") is True
    assert getattr(settings, f"{field}_RATE_LIMIT_MAX_REQUESTS") == default_max
    assert getattr(settings, f"{field}_RATE_LIMIT_WINDOW_SECONDS") == 600


@pytest.mark.parametrize(("field", "env", "default_max"), _BUDGETS, ids=[b[0] for b in _BUDGETS])
def test_aliases(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str, env: str, default_max: int) -> None:
    settings = _make_settings(
        monkeypatch,
        tmp_path,
        **{
            f"{env}_RATE_LIMIT_ENABLED": "false",
            f"{env}_RATE_LIMIT_MAX_REQUESTS": "3",
            f"{env}_RATE_LIMIT_WINDOW_SECONDS": "60",
        },
    )
    assert getattr(settings, f"{field}_RATE_LIMIT_ENABLED") is False
    assert getattr(settings, f"{field}_RATE_LIMIT_MAX_REQUESTS") == 3
    assert getattr(settings, f"{field}_RATE_LIMIT_WINDOW_SECONDS") == 60


@pytest.mark.parametrize(("field", "env", "default_max"), _BUDGETS, ids=[b[0] for b in _BUDGETS])
@pytest.mark.parametrize("suffix", ["MAX_REQUESTS", "WINDOW_SECONDS"])
def test_non_positive_values_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str, env: str, default_max: int, suffix: str
) -> None:
    """A zero budget or window would refuse every request; turning it off is what ENABLED is for."""
    variable = f"{env}_RATE_LIMIT_{suffix}"
    with pytest.raises(ValidationError, match=variable):
        _make_settings(monkeypatch, tmp_path, **{variable: "0"})
