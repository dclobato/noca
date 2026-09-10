#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Author and check email template overrides before a deployment reads them.

The running process refuses to start on an invalid override tree, which is the
right behavior and a poor way to find out. This script runs exactly the checks
Web and Arena run at startup, plus the two things only an author needs: the
packaged default as a starting point, and a preview of what a key renders to.

Usage -- the module and the root come first, every option after them::

    uv run python scripts/validate_email_templates.py <web|arena> <root> [options]

That order is required, not a convention: `--export` and `--preview` take an
optional list of keys, so an option placed before the positionals swallows them
as key names and argparse then reports the positionals as missing.

    arena /srv/overrides --export reset_password    # right
    --export reset_password arena /srv/overrides    # wrong: eats both positionals

Operations (the default is to validate the tree)::

    --list                 stable keys and the placeholders each one declares
    --export [KEY ...]     write packaged defaults into <root>/<module>/
    --preview [KEY ...]    render with the catalogue's sample values
    --force                let --export overwrite files that already exist

Exit codes: 0 when the requested operation succeeded and the tree is valid,
1 otherwise. Stale ``based_on`` digests are warnings and do not fail the run.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

# Running this file by path puts `scripts/` first on `sys.path`, so any package
# under `scripts/` that shares a name with a workspace package would shadow it --
# which is what `scripts/web/` did until its `__init__.py` was removed, breaking
# `web.email_templates` here while `arena` worked. The directory layout is
# guarded by a test now; the repository root goes first regardless, because this
# script should not depend on nobody ever adding such a directory again.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.services.email_templates import (  # noqa: E402
    EmailTemplateDefinition,
    EmailTemplateError,
    FilesystemOverrideSource,
    build_module_registry,
    default_digest,
    load_email_template,
    validate_override_tree,
)

MODULES = ("web", "arena")


def _definitions(module: str) -> tuple[EmailTemplateDefinition, ...]:
    """Import one module's catalogue without importing its configuration.

    Both catalogue packages are deliberately settings-free at import time, so
    this runs on a host that holds the override tree and nothing else.
    """
    if module == "web":
        from web.email_templates import WEB_EMAIL_TEMPLATE_DEFINITIONS

        return WEB_EMAIL_TEMPLATE_DEFINITIONS
    from arena.email_templates import ARENA_EMAIL_TEMPLATE_DEFINITIONS

    return ARENA_EMAIL_TEMPLATE_DEFINITIONS


def main(argv: list[str] | None = None) -> int:
    """Run one operation against a module's override namespace.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    args = _parse_args(argv)
    definitions = _definitions(args.module)
    directory = args.root / args.module

    if args.list:
        _list_keys(definitions)
        return 0
    if args.export is not None:
        return _export(definitions, directory, keys=args.export, force=args.force)
    if args.preview is not None:
        return _preview(definitions, args.root, args.module, keys=args.preview)
    return _validate(definitions, args.root, args.module)


class _OrderedArgumentParser(argparse.ArgumentParser):
    """An argument parser that explains the one mistake this grammar invites."""

    def error(self, message: str) -> None:  # type: ignore[override]
        """Add the ordering hint when the positionals were swallowed by an option.

        `--export`/`--preview` take an optional key list, so putting one before
        the positionals consumes them as keys and argparse then reports only
        that `module` and `root` are missing -- true, and useless for working out
        why. Naming the real cause here costs one line and saves the guess.
        """
        if "module" in message and "root" in message:
            message = (
                f"{message}\n\nthe module and the root must come first, before any option: "
                "--export and --preview take an optional list of keys and will otherwise "
                "consume them.\n  correct: %(prog)s arena /srv/overrides --export reset_password" % {"prog": self.prog}
            )
        super().error(message)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the module, the override root, and the requested operation."""
    parser = _OrderedArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="%(prog)s <web|arena> <root> [options]",
        epilog=(
            "The module and the root are positional and must come first; every option "
            "follows them.\n"
            "  %(prog)s arena /srv/overrides                          validate the tree\n"
            "  %(prog)s arena /srv/overrides --list                   keys and placeholders\n"
            "  %(prog)s arena /srv/overrides --export                 export every default\n"
            "  %(prog)s arena /srv/overrides --export reset_password  export one default\n"
            "  %(prog)s arena /srv/overrides --preview reset_password render with samples"
        ),
    )
    parser.add_argument("module", choices=MODULES, help="Which catalogue and namespace to work with (first argument).")
    parser.add_argument("root", type=Path, help="Override root directory holding web/ and arena/ (second argument).")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--list", action="store_true", help="List keys and their placeholder contracts.")
    operation.add_argument(
        "--export",
        nargs="*",
        metavar="KEY",
        help="Write packaged defaults into the namespace (all keys when none are named).",
    )
    operation.add_argument(
        "--preview",
        nargs="*",
        metavar="KEY",
        help="Render keys with their sample values (all keys when none are named).",
    )
    parser.add_argument("--force", action="store_true", help="Allow --export to overwrite existing files.")
    return parser.parse_args(argv)


def _selected(definitions: tuple[EmailTemplateDefinition, ...], keys: Sequence[str]) -> list[EmailTemplateDefinition]:
    """Return the requested definitions, or all of them when none are named.

    Raises:
        EmailTemplateError: If a named key is not in the catalogue.
    """
    if not keys:
        return list(definitions)
    by_key = {definition.key: definition for definition in definitions}
    unknown = sorted(set(keys) - set(by_key))
    if unknown:
        raise EmailTemplateError(f"unknown template keys: {', '.join(unknown)}")
    return [by_key[key] for key in keys]


def _list_keys(definitions: tuple[EmailTemplateDefinition, ...]) -> None:
    """Print each key, its digest, and the placeholders it accepts."""
    for definition in sorted(definitions, key=lambda item: item.key):
        print(f"{definition.key}  (based_on {default_digest(definition)})")
        print(f"  subject: {_names(definition.subject_placeholders)}")
        print(f"  body:    {_names(definition.body_placeholders)}")
        print(f"  required: {_names(definition.required_placeholders)}")


def _names(placeholders: frozenset[str]) -> str:
    """Render a placeholder set as a stable, readable list."""
    return ", ".join(sorted(placeholders)) if placeholders else "(none)"


def _export(
    definitions: tuple[EmailTemplateDefinition, ...],
    directory: Path,
    *,
    keys: Sequence[str],
    force: bool,
) -> int:
    """Copy packaged defaults into the namespace, stamped with their digest.

    The packaged file is copied verbatim rather than re-serialized, so what an
    operator starts editing is exactly the shipped wording, and a ``based_on``
    line is appended so a later upstream change can be reported as drift.
    """
    directory.mkdir(parents=True, exist_ok=True)
    for definition in _selected(definitions, keys):
        target = directory / f"{definition.key}.toml"
        if target.exists() and not force:
            print(f"skipped {target} (exists; use --force to overwrite)")
            continue
        content = definition.default_path.read_text(encoding="utf-8")
        stamped = f'{content.rstrip()}\n\nbased_on = "{default_digest(definition)}"\n'
        target.write_text(stamped, encoding="utf-8")
        print(f"wrote {target}")
    return 0


def _preview(
    definitions: tuple[EmailTemplateDefinition, ...],
    root: Path,
    module: str,
    *,
    keys: Sequence[str],
) -> int:
    """Render keys the way the deployment would, overrides included."""
    selected = _selected(definitions, keys)
    registry = build_module_registry(definitions, namespace=module, override_root=root)
    source = registry.override_lookup
    for definition in selected:
        rendered = registry.render(
            definition.key,
            brand_name="NOCA",
            context=definition.sample_values,
        )
        print(f"--- {definition.key} [{_origin(source, definition.key)}] ---")
        print(f"Subject: {rendered.subject}")
        print(rendered.body)
        print()
    return 0


def _origin(source: object, key: str) -> str:
    """Say what the render above actually came from.

    The presence of a file is not the answer: a file the renderer rejected leaves
    the packaged default in effect, and a preview that called that an override
    would confirm exactly the change that is not being sent. The source's own
    post-render state is the only honest label.
    """
    if not isinstance(source, FilesystemOverrideSource):
        return "packaged default"
    state = {item.key: item for item in source.describe()}.get(key)
    if state is None or not (state.active or state.error):
        return "packaged default"
    if state.error is not None:
        return f"packaged default; override rejected: {state.error}"
    return "override, stale based_on" if state.stale else "override"


def _validate(definitions: tuple[EmailTemplateDefinition, ...], root: Path, module: str) -> int:
    """Run the startup check over a tree and report drift as a warning."""
    directory = root / module
    if not directory.is_dir():
        print(f"{directory} does not exist: every {module} email renders from its packaged default.")
        return 0
    problems = validate_override_tree(root, module, definitions)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    stale = _stale(definitions, directory)
    for key, based_on, current in stale:
        print(
            f"warning: {key}.toml was written from default {based_on}, which is now {current}; "
            "re-check it against the shipped wording.",
            file=sys.stderr,
        )
    if problems:
        print(f"{len(problems)} invalid file(s): {module} would refuse to start.", file=sys.stderr)
        return 1
    print(f"{directory} is valid.")
    return 0


def _stale(
    definitions: tuple[EmailTemplateDefinition, ...],
    directory: Path,
) -> list[tuple[str, str, str]]:
    """Return ``(key, recorded digest, current digest)`` for drifted overrides."""
    drifted = []
    for definition in definitions:
        path = directory / f"{definition.key}.toml"
        if not path.is_file():
            continue
        try:
            template = load_email_template(path)
        except EmailTemplateError:
            continue
        current = default_digest(definition)
        if template.based_on is not None and template.based_on != current:
            drifted.append((definition.key, template.based_on, current))
    return drifted


if __name__ == "__main__":
    try:
        sys.exit(main())
    except EmailTemplateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
