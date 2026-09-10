#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Wiring one module's catalogue to the deployment's override configuration.

Web and Arena differ only in which catalogue and which namespace they bring, so
the two lines that turn a configured directory into a registry live here rather
than twice in module packages that shared code must not import.
"""

from __future__ import annotations

from pathlib import Path

from shared.services.email_templates.catalogue import EmailTemplateDefinition
from shared.services.email_templates.grammar import EmailTemplateError
from shared.services.email_templates.overrides import FilesystemOverrideSource, validate_override_tree
from shared.services.email_templates.registry import EmailTemplateRegistry


def build_module_registry(
    definitions: tuple[EmailTemplateDefinition, ...],
    *,
    namespace: str,
    override_root: Path | None,
) -> EmailTemplateRegistry:
    """Build a module registry, with filesystem overrides when configured.

    Args:
        definitions: The module's catalogue entries.
        namespace: Subdirectory of the override root, ``web`` or ``arena``.
        override_root: Configured override directory, or ``None`` when disabled.

    Returns:
        A registry rendering packaged defaults, overridden per key when a valid
        file exists under the namespace.
    """
    lookup = None if override_root is None else FilesystemOverrideSource(override_root, namespace, definitions)
    return EmailTemplateRegistry(definitions, override_lookup=lookup)


def ensure_valid_override_tree(
    definitions: tuple[EmailTemplateDefinition, ...],
    *,
    namespace: str,
    override_root: Path | None,
) -> None:
    """Refuse to continue while any override file in the namespace is invalid.

    This is the startup check, and it fails closed on purpose: it is the one
    validation every replica runs against the same tree, so a mistake caught
    here stops a deploy instead of silently reaching only the replicas that
    happened to reload the file.

    Args:
        definitions: The module's catalogue entries.
        namespace: Subdirectory of the override root, ``web`` or ``arena``.
        override_root: Configured override directory, or ``None`` when disabled.

    Raises:
        EmailTemplateError: If any file in the namespace is invalid or names an
            unknown key. The message lists every problem, one per line.
    """
    if override_root is None:
        return
    problems = validate_override_tree(override_root, namespace, definitions)
    if not problems:
        return
    listing = "\n".join(f"  {problem}" for problem in problems)
    raise EmailTemplateError(f"invalid email template overrides in {override_root / namespace}:\n{listing}")
