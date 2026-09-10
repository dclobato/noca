#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Constrained, backend-independent rendering for outbound email templates.

The package is split by responsibility: :mod:`grammar` owns the placeholder
format, its substitution, and the output limits; :mod:`catalogue` owns the
module-facing declaration types and the override lookup contract;
:mod:`loader` owns TOML loading and contract validation; :mod:`registry` binds a
module catalogue to rendering; :mod:`overrides` implements the host-managed
filesystem override source; :mod:`module_registry` wires a module's catalogue to
the deployment's configuration. Importers use this façade rather than the
submodules, so the split stays an internal decision.
"""

from __future__ import annotations

from shared.services.email_templates.catalogue import (
    EmailTemplate,
    EmailTemplateDefinition,
    EmailTemplateOverrideLookup,
    RenderedEmail,
)
from shared.services.email_templates.grammar import (
    BODY_MAX_BYTES,
    GLOBAL_PLACEHOLDERS,
    SUBJECT_MAX_CHARACTERS,
    EmailTemplateError,
)
from shared.services.email_templates.loader import load_email_template, validate_email_template
from shared.services.email_templates.module_registry import (
    build_module_registry,
    ensure_valid_override_tree,
)
from shared.services.email_templates.overrides import (
    TEMPLATE_SUFFIX,
    FilesystemOverrideSource,
    OverrideProblem,
    OverrideState,
    default_digest,
    validate_override_tree,
)
from shared.services.email_templates.registry import EmailTemplateRegistry
from shared.services.email_templates.visibility import EmailTemplateVisibility, inspect_email_templates

__all__ = [
    "BODY_MAX_BYTES",
    "GLOBAL_PLACEHOLDERS",
    "SUBJECT_MAX_CHARACTERS",
    "TEMPLATE_SUFFIX",
    "EmailTemplate",
    "EmailTemplateDefinition",
    "EmailTemplateError",
    "EmailTemplateOverrideLookup",
    "EmailTemplateRegistry",
    "EmailTemplateVisibility",
    "FilesystemOverrideSource",
    "OverrideProblem",
    "OverrideState",
    "RenderedEmail",
    "build_module_registry",
    "default_digest",
    "ensure_valid_override_tree",
    "inspect_email_templates",
    "load_email_template",
    "validate_email_template",
    "validate_override_tree",
]
