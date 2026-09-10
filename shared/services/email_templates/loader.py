#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""TOML loading and contract validation for one template unit.

Loading answers "is this a well-formed template file?"; validation answers
"does this template honour the contract its catalogue declares?". Both apply to
a packaged default and to a backend-supplied override alike.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from shared.services.email_templates.catalogue import EmailTemplate, EmailTemplateDefinition
from shared.services.email_templates.grammar import (
    GLOBAL_PLACEHOLDERS,
    EmailTemplateError,
    placeholders,
    validate_output_sizes,
)

_TEMPLATE_FIELDS = frozenset({"subject", "body", "based_on"})


def load_email_template(path: Path) -> EmailTemplate:
    """Load one subject/body unit from a TOML file.

    Args:
        path: TOML file containing ``subject`` and ``body`` string fields.

    Returns:
        The parsed template unit.

    Raises:
        EmailTemplateError: If the file cannot be read or has an invalid schema.
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise EmailTemplateError(f"{path}: cannot load template: {exc}") from exc
    unknown_keys = set(data) - _TEMPLATE_FIELDS
    if unknown_keys:
        names = ", ".join(sorted(unknown_keys))
        raise EmailTemplateError(f"{path}: unknown fields: {names}")
    subject = data.get("subject")
    body = data.get("body")
    based_on = data.get("based_on")
    if not isinstance(subject, str) or not isinstance(body, str):
        raise EmailTemplateError(f"{path}: subject and body must be strings")
    if based_on is not None and not isinstance(based_on, str):
        raise EmailTemplateError(f"{path}: based_on must be a string when present")
    return EmailTemplate(subject=subject, body=body, based_on=based_on)


def validate_email_template(template: EmailTemplate, definition: EmailTemplateDefinition) -> None:
    """Validate a parsed template against its catalogue definition.

    Args:
        template: The parsed subject/body unit.
        definition: The catalogue contract the unit must honour.

    Raises:
        EmailTemplateError: If the template uses an undeclared placeholder, omits
            a required one, or exceeds a subject or body limit.
    """
    subject_location = f"{definition.key} subject"
    body_location = f"{definition.key} body"
    subject_names = placeholders(template.subject, location=subject_location)
    body_names = placeholders(template.body, location=body_location)
    unknown_subject = subject_names - definition.subject_placeholders - GLOBAL_PLACEHOLDERS
    unknown_body = body_names - definition.body_placeholders - GLOBAL_PLACEHOLDERS
    if unknown_subject:
        names = ", ".join(sorted(unknown_subject))
        raise EmailTemplateError(f"{subject_location}: unknown placeholders: {names}")
    if unknown_body:
        names = ", ".join(sorted(unknown_body))
        raise EmailTemplateError(f"{body_location}: unknown placeholders: {names}")
    missing_required = definition.required_placeholders - subject_names - body_names
    if missing_required:
        names = ", ".join(sorted(missing_required))
        raise EmailTemplateError(f"{definition.key}: missing required placeholders: {names}")
    validate_output_sizes(template.subject, template.body, location=definition.key)
