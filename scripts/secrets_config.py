#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Manage NOCA encryption key configuration and inspect encrypted columns.

Usage:
    uv run python scripts/secrets_config.py generate
    uv run python scripts/secrets_config.py rotate
    uv run python scripts/secrets_config.py list
    uv run python scripts/secrets_config.py set-active -v v1
    uv run python scripts/secrets_config.py set-active --latest
    uv run python scripts/secrets_config.py analyze-column --table arena_users --column _otp_secret
"""

from __future__ import annotations

import argparse
import asyncio
import re
import secrets as py_secrets
from collections.abc import Sequence
from pathlib import Path

from secrets_manager import SecretsConfig
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DEFAULT_ENV_FILE = ".env.crypto"
DEFAULT_VERSION = "v1"
DEFAULT_KEY_BYTES = 32
DEFAULT_SALT_BYTES = 16
VERSION_PREFIX = "v"
VERSION_SEPARATOR = ":"
UNVERSIONED_LABEL = "<unversioned>"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _new_key(byte_count: int) -> str:
    """Generate high-entropy key material suitable for SecretsManager derivation."""
    if byte_count <= 0:
        raise ValueError("byte count must be positive")
    return py_secrets.token_urlsafe(byte_count)


def _new_salt(byte_count: int) -> bytes:
    """Generate a random salt."""
    if byte_count <= 0:
        raise ValueError("byte count must be positive")
    return py_secrets.token_bytes(byte_count)


def _load_config(env_file: Path) -> SecretsConfig:
    """Load a SecretsManager configuration file."""
    return SecretsConfig.from_file(str(env_file))


def _save_config(config: SecretsConfig, env_file: Path) -> None:
    """Persist a SecretsManager configuration with restrictive permissions."""
    config.to_file(str(env_file))
    env_file.chmod(0o600)


def generate_config(args: argparse.Namespace) -> int:
    """Generate the initial encryption configuration file."""
    env_file = Path(args.env_file)
    version = str(args.version).lower()
    if env_file.exists() and not args.force:
        print(f"Configuration already exists: {env_file}")
        print("Use --force to replace it, or rotate to add a new key version.")
        return 2

    config = SecretsConfig(
        keys={version: {"key": _new_key(args.key_bytes), "salt": _new_salt(args.salt_bytes)}},
        active_version=version,
    )
    _save_config(config, env_file)

    print(f"Generated configuration: {env_file}")
    print(f"Active version: {version}")
    print("Permissions: 600")
    print("Keep this file out of git and back it up securely.")
    return 0


def _version_number(version: str) -> int:
    """Extract the numeric portion from a vN version name."""
    if not version.startswith(VERSION_PREFIX):
        return 0
    try:
        return int(version[len(VERSION_PREFIX) :])
    except ValueError:
        return 0


def _next_version(versions: Sequence[str]) -> str:
    """Return the next vN version for the configured key set."""
    highest = max((_version_number(version) for version in versions), default=0)
    return f"{VERSION_PREFIX}{highest + 1}"


def rotate_config(args: argparse.Namespace) -> int:
    """Add a new key version and make it active."""
    env_file = Path(args.env_file)
    config = _load_config(env_file)
    new_version = (args.new_version or _next_version(tuple(config.keys))).lower()

    if new_version in config.keys:
        print(f"Version already exists: {new_version}")
        return 2

    if args.dry_run:
        print(f"Would create version {new_version} and set it active in {env_file}")
        return 0

    config.keys[new_version] = {"key": _new_key(args.key_bytes), "salt": _new_salt(args.salt_bytes)}
    config.active_version = new_version
    _save_config(config, env_file)

    print(f"Created version: {new_version}")
    print(f"Active version: {new_version}")
    print(f"Updated configuration: {env_file}")
    print("Existing encrypted data should be re-encrypted before removing old versions.")
    return 0


def list_config(args: argparse.Namespace) -> int:
    """List key versions available in the configuration file."""
    env_file = Path(args.env_file)
    config = _load_config(env_file)
    versions = _sorted_versions(tuple(config.keys))

    print(f"Configuration: {env_file}")
    print(f"Active version: {config.active_version}")
    print(f"Available versions ({len(versions)}):")
    for version in versions:
        suffix = " [active]" if version == config.active_version else ""
        print(f"  - {version}{suffix}")
    return 0


def _sorted_versions(versions: Sequence[str]) -> list[str]:
    """Return versions sorted by numeric suffix and then name."""
    return sorted(versions, key=lambda version: (_version_number(version), version))


def _latest_version(versions: Sequence[str]) -> str:
    """Return the latest configured version."""
    if not versions:
        raise ValueError("No key versions are configured")
    return _sorted_versions(versions)[-1]


def set_active_config(args: argparse.Namespace) -> int:
    """Set the active key version without creating new key material."""
    env_file = Path(args.env_file)
    config = _load_config(env_file)
    version = _latest_version(tuple(config.keys)) if args.latest else str(args.version).lower()

    if version not in config.keys:
        print(f"Version not found: {version}")
        print(f"Available versions: {', '.join(_sorted_versions(tuple(config.keys)))}")
        return 2

    if config.active_version == version:
        print(f"Active version already set to: {version}")
        return 0

    config.active_version = version
    _save_config(config, env_file)
    print(f"Active version set to: {version}")
    print(f"Updated configuration: {env_file}")
    return 0


def _quote_identifier(identifier: str) -> str:
    """Quote a SQL identifier after strict validation."""
    parts = identifier.split(".")
    for part in parts:
        if not _IDENTIFIER_RE.fullmatch(part):
            raise ValueError(f"Invalid SQL identifier: {identifier}")
    return ".".join(f'"{part}"' for part in parts)


async def analyze_column_usage(args: argparse.Namespace) -> int:
    """Analyze key-version prefixes in a database column."""
    database_url = args.database_url
    if database_url is None:
        from web.config import settings

        database_url = settings.db_url

    table_name = _quote_identifier(args.table)
    column_name = _quote_identifier(args.column)
    version_expression = (
        f"case when position('{VERSION_SEPARATOR}' in {column_name}) > 0 "
        f"then split_part({column_name}, '{VERSION_SEPARATOR}', 1) "
        f"else '{UNVERSIONED_LABEL}' end"
    )
    query = text(
        f"select {version_expression} as version, count(*) as record_count "
        f"from {table_name} "
        f"where {column_name} is not null and {column_name} <> '' "
        "group by version order by version"
    )

    engine = create_async_engine(database_url, echo=False)
    try:
        async with engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
    finally:
        await engine.dispose()

    total = sum(int(row["record_count"]) for row in rows)
    print(f"Column: {args.table}.{args.column}")
    print(f"Non-empty encrypted values: {total}")
    for row in rows:
        count = int(row["record_count"])
        percentage = (count / total * 100.0) if total else 0.0
        print(f"  - {row['version']}: {count} ({percentage:.2f}%)")
    if not rows:
        print("  No encrypted values found.")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(description="Manage NOCA SecretsManager configuration", allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate an initial .env.crypto file")
    generate.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    generate.add_argument("--version", default=DEFAULT_VERSION)
    generate.add_argument("--key-bytes", type=int, default=DEFAULT_KEY_BYTES)
    generate.add_argument("--salt-bytes", type=int, default=DEFAULT_SALT_BYTES)
    generate.add_argument("--force", action="store_true", help="Replace an existing configuration file")
    generate.set_defaults(func=generate_config)

    rotate = subparsers.add_parser("rotate", help="Add a new key version and activate it")
    rotate.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    rotate.add_argument("--new-version", default=None)
    rotate.add_argument("--key-bytes", type=int, default=DEFAULT_KEY_BYTES)
    rotate.add_argument("--salt-bytes", type=int, default=DEFAULT_SALT_BYTES)
    rotate.add_argument("--dry-run", action="store_true", help="Show planned rotation without writing")
    rotate.set_defaults(func=rotate_config)

    list_keys = subparsers.add_parser("list", help="List configured key versions")
    list_keys.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    list_keys.set_defaults(func=list_config)

    set_active = subparsers.add_parser("set-active", help="Set the active key version")
    set_active.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    version_group = set_active.add_mutually_exclusive_group(required=True)
    version_group.add_argument("-v", "--version", help="Existing key version to activate")
    version_group.add_argument("--latest", action="store_true", help="Activate the latest configured version")
    set_active.set_defaults(func=set_active_config)

    analyze = subparsers.add_parser("analyze-column", help="Analyze key usage in a table column")
    analyze.add_argument("--table", required=True, help="Table name, optionally schema-qualified")
    analyze.add_argument("--column", required=True, help="Encrypted column name")
    analyze.add_argument("--database-url", default=None, help="Override database URL; defaults to NOCA settings")
    analyze.set_defaults(func=analyze_column_usage)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI."""
    args = _build_parser().parse_args(argv)
    result = args.func(args)
    if asyncio.iscoroutine(result):
        return asyncio.run(result)
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
