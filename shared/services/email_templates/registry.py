#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The registry that binds a module catalogue to rendering.

This is the only place that composes the other responsibilities: it loads and
validates every packaged default at construction, consults an optional override
backend at render time, and revalidates whatever it is about to render.
"""

from __future__ import annotations

from collections.abc import Mapping

from shared.services.email_templates.catalogue import (
    EmailTemplate,
    EmailTemplateDefinition,
    EmailTemplateOverrideLookup,
    RenderedEmail,
)
from shared.services.email_templates.grammar import (
    EmailTemplateError,
    substitute,
    validate_output_sizes,
)
from shared.services.email_templates.loader import load_email_template, validate_email_template


class EmailTemplateRegistry:
    """Validated module catalogue with packaged-default fallback rendering."""

    def __init__(
        self,
        definitions: tuple[EmailTemplateDefinition, ...],
        *,
        override_lookup: EmailTemplateOverrideLookup | None = None,
    ) -> None:
        """Load and validate every packaged default in a module catalogue.

        Args:
            definitions: The module's catalogue entries.
            override_lookup: Optional backend consulted before each render.

        Raises:
            EmailTemplateError: If a key is duplicated or a default is invalid.
        """
        self._definitions = {definition.key: definition for definition in definitions}
        if len(self._definitions) != len(definitions):
            raise EmailTemplateError("email template catalogue contains duplicate keys")
        self._defaults: dict[str, EmailTemplate] = {}
        self._override_lookup = override_lookup
        for definition in definitions:
            template = load_email_template(definition.default_path)
            validate_email_template(template, definition)
            self._defaults[definition.key] = template

    @property
    def definitions(self) -> Mapping[str, EmailTemplateDefinition]:
        """Expose catalogue definitions as a mapping contract."""
        return self._definitions

    @property
    def override_lookup(self) -> EmailTemplateOverrideLookup | None:
        """Expose the override backend, so a caller can report what it is serving."""
        return self._override_lookup

    def render(self, key: str, *, brand_name: str, context: Mapping[str, object]) -> RenderedEmail:
        """Render one packaged template or a backend-provided override.

        Args:
            key: Stable catalogue key.
            brand_name: Value for the globally declared ``brand_name``.
            context: Values for the key's declared placeholders.

        Returns:
            The rendered subject and body.

        Raises:
            EmailTemplateError: If the key is unknown, the template is invalid,
                or the context lacks a declared placeholder.
        """
        try:
            definition = self._definitions[key]
        except KeyError as exc:
            raise EmailTemplateError(f"unknown email template key {key!r}") from exc
        template = None if self._override_lookup is None else self._override_lookup.get(definition)
        if template is None:
            template = self._defaults[key]
        validate_email_template(template, definition)
        render_context = {"brand_name": brand_name, **context}
        subject = substitute(template.subject, render_context, location=f"{key} subject")
        body = substitute(template.body, render_context, location=f"{key} body").rstrip()
        validate_output_sizes(subject, body, location=key)
        return RenderedEmail(subject=subject, body=body)

    def render_samples(self, *, brand_name: str) -> Mapping[str, RenderedEmail]:
        """Render every packaged default with its declared sample context."""
        return {
            key: self.render(key, brand_name=brand_name, context=definition.sample_values)
            for key, definition in self._definitions.items()
        }
