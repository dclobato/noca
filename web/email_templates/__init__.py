#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Web-owned email template catalogue and renderer.

The registry is built on first use rather than at import, for two reasons: it
must read `NOCA_EMAIL_TEMPLATE_OVERRIDE_DIR` from settings, and the validation
CLI has to import this catalogue on a host that has no Web configuration at all.
Nothing here reaches into `web.config` at module scope for the same reason.
"""

from __future__ import annotations

from functools import cache

from shared.services.email_templates import (
    EmailTemplateRegistry,
    RenderedEmail,
    build_module_registry,
    ensure_valid_override_tree,
)
from web.email_templates.catalogue import WEB_EMAIL_TEMPLATE_DEFINITIONS

__all__ = [
    "WEB_EMAIL_NAMESPACE",
    "WEB_EMAIL_TEMPLATE_DEFINITIONS",
    "render_email",
    "validate_email_template_overrides",
    "web_email_templates",
]

WEB_EMAIL_NAMESPACE = "web"


@cache
def web_email_templates() -> EmailTemplateRegistry:
    """Return Web's registry, wired to the configured override directory.

    Returns:
        The process-wide registry. Cached: it validates every packaged default
        on construction, and the override source it holds keeps the per-key
        cache that decides when a published file is reread.
    """
    from web.config import settings

    return build_module_registry(
        WEB_EMAIL_TEMPLATE_DEFINITIONS,
        namespace=WEB_EMAIL_NAMESPACE,
        override_root=settings.EMAIL_TEMPLATE_OVERRIDE_DIR,
    )


def validate_email_template_overrides() -> None:
    """Refuse to start while any published Web override is invalid.

    Raises:
        EmailTemplateError: If a file under the ``web/`` namespace is invalid or
            names a key the catalogue does not declare.
    """
    from web.config import settings

    ensure_valid_override_tree(
        WEB_EMAIL_TEMPLATE_DEFINITIONS,
        namespace=WEB_EMAIL_NAMESPACE,
        override_root=settings.EMAIL_TEMPLATE_OVERRIDE_DIR,
    )


def render_email(key: str, **context: object) -> RenderedEmail:
    """Render a Web email from its stable catalogue key.

    Args:
        key: Stable snake-case template key.
        **context: Values for placeholders declared by the template.

    Returns:
        Rendered subject and plain-text body.
    """
    from web.config import settings

    return web_email_templates().render(key, brand_name=settings.BRAND_NAME, context=context)
