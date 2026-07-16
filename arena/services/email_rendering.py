#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared renderer for Arena plain-text email templates.

Arena email bodies live in ``arena/template/emails/`` and render through a
standalone Jinja environment (not the request-time ``templates.env``), so the
``brand_name`` template global is not available to them. This module centralizes
the environment and always injects ``brand_name`` from settings, so every email
shows the configured brand and the four email services stop duplicating the same
environment/render boilerplate.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from arena.config import settings

_EMAIL_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "template" / "emails"


@lru_cache(maxsize=1)
def _email_template_environment() -> Environment:
    """Return the cached Jinja2 environment for plain-text Arena email templates.

    Returns:
        Environment: Shared environment loading ``arena/template/emails/``.
    """
    return Environment(
        loader=FileSystemLoader(str(_EMAIL_TEMPLATE_DIR)),
        autoescape=False,
        trim_blocks=False,
        lstrip_blocks=False,
        undefined=StrictUndefined,
    )


def render_email(template_name: str, **context: object) -> str:
    """Render a named Arena email template with ``brand_name`` always injected.

    Args:
        template_name: Filename inside ``arena/template/emails/``.
        **context: Template variables. An explicit ``brand_name`` overrides the
            settings default.

    Returns:
        str: Rendered plain-text body, trailing whitespace stripped.
    """
    render_context: dict[str, object] = {"brand_name": settings.BRAND_NAME}
    render_context.update(context)
    template = _email_template_environment().get_template(template_name)
    return template.render(**render_context).rstrip()
