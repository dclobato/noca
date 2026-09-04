#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for shared Arena location flag template macros."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "arena" / "template"


class _Request:
    """Minimal request stand-in that resolves static asset URLs."""

    def url_for(self, route_name: str, **path_params: Any) -> str:
        """Return a predictable URL for macro assertions.

        Args:
            route_name: Route name requested by the template.
            **path_params: Route parameters, including the static asset path.

        Returns:
            str: Predictable test URL.
        """
        assert route_name == "static_vendor"
        return f"/static/vendor/{path_params['path']}"


def _macros() -> Any:
    """Load the Arena macro module with HTML autoescaping enabled."""
    environment = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(default_for_string=True),
    )
    return environment.get_template("_macros.html").module


def _location(
    country_code: str,
    country_name: str,
    subdivision_code: str,
    subdivision_name: str,
) -> SimpleNamespace:
    """Build a template-compatible location object."""
    return SimpleNamespace(
        country_code=country_code,
        country_name=country_name,
        subdivision_code=subdivision_code,
        subdivision_name=subdivision_name,
    )


def test_brazilian_location_renders_country_and_state_flags_without_text() -> None:
    """Brazilian locations expose only accessible country and state flags."""
    rendered = str(
        _macros().render_location(
            _Request(),
            _location("BR", "Brazil", "BR-RS", "Rio Grande do Sul"),
        )
    )

    assert "/img/state-flags/BR.svg" in rendered
    assert "arena-country-flag--circular" in rendered
    assert "/img/state-flags/RS.svg" in rendered
    assert 'alt="Brazil flag"' in rendered
    assert 'alt="Rio Grande do Sul flag"' in rendered
    assert ">Brazil<" not in rendered
    assert ">Rio Grande do Sul<" not in rendered


def test_non_brazilian_location_keeps_flag_country_and_subdivision_text() -> None:
    """Non-Brazilian locations retain the existing text presentation."""
    rendered = str(
        _macros().render_location(
            _Request(),
            _location("US", "United States", "US-CA", "California"),
        )
    )

    assert "/img/flags/us.svg" in rendered
    assert "/img/state-flags/BR.svg" not in rendered
    assert "arena-country-flag--circular" not in rendered
    assert "/img/state-flags/" not in rendered
    assert "United States" in rendered
    assert ", California" in rendered


def test_decorative_state_flag_is_limited_to_brazil() -> None:
    """Subdivision cells get a decorative state flag only for Brazil."""
    macros = _macros()
    brazil = str(macros.render_state_flag(_Request(), "BR", "BR-SP", "São Paulo", True))
    elsewhere = str(macros.render_state_flag(_Request(), "US", "US-CA", "California", True))

    assert "/img/state-flags/SP.svg" in brazil
    assert 'alt=""' in brazil
    assert 'aria-hidden="true"' in brazil
    assert elsewhere.strip() == ""
