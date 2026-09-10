#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena-owned email template catalogue and constrained renderer.

The registry is built on first use rather than at import, for two reasons: it
must read `NOCA_EMAIL_TEMPLATE_OVERRIDE_DIR` from settings, and the validation
CLI has to import this catalogue on a host that has no Arena configuration at
all. Nothing here reaches into `arena.config` at module scope for the same reason.
"""

from __future__ import annotations

from functools import cache

from arena.email_templates.catalogue_classes import CLASS_EMAIL_TEMPLATE_DEFINITIONS
from arena.email_templates.catalogue_registration import REGISTRATION_EMAIL_TEMPLATE_DEFINITIONS
from arena.email_templates.catalogue_security import SECURITY_EMAIL_TEMPLATE_DEFINITIONS
from shared.services.email_templates import (
    EmailTemplateRegistry,
    RenderedEmail,
    build_module_registry,
    ensure_valid_override_tree,
)

ARENA_EMAIL_TEMPLATE_DEFINITIONS = (
    *REGISTRATION_EMAIL_TEMPLATE_DEFINITIONS,
    *CLASS_EMAIL_TEMPLATE_DEFINITIONS,
    *SECURITY_EMAIL_TEMPLATE_DEFINITIONS,
)
ARENA_EMAIL_NAMESPACE = "arena"


@cache
def arena_email_templates() -> EmailTemplateRegistry:
    """Return Arena's registry, wired to the configured override directory.

    Returns:
        The process-wide registry. Cached: it validates every packaged default
        on construction, and the override source it holds keeps the per-key
        cache that decides when a published file is reread.
    """
    from arena.config import settings

    return build_module_registry(
        ARENA_EMAIL_TEMPLATE_DEFINITIONS,
        namespace=ARENA_EMAIL_NAMESPACE,
        override_root=settings.EMAIL_TEMPLATE_OVERRIDE_DIR,
    )


def validate_email_template_overrides() -> None:
    """Refuse to start while any published Arena override is invalid.

    Raises:
        EmailTemplateError: If a file under the ``arena/`` namespace is invalid
            or names a key the catalogue does not declare.
    """
    from arena.config import settings

    ensure_valid_override_tree(
        ARENA_EMAIL_TEMPLATE_DEFINITIONS,
        namespace=ARENA_EMAIL_NAMESPACE,
        override_root=settings.EMAIL_TEMPLATE_OVERRIDE_DIR,
    )


def render_email(key: str, **context: object) -> RenderedEmail:
    """Render an Arena email from its stable catalogue key.

    Args:
        key: Stable snake-case template key.
        **context: Values for placeholders declared by the template.

    Returns:
        Rendered subject and plain-text body.
    """
    from arena.config import settings

    return arena_email_templates().render(key, brand_name=settings.BRAND_NAME, context=context)
