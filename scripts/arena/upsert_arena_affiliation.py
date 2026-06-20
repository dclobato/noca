#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Upsert an Arena affiliation.

Usage:
    uv run python scripts/arena/upsert_arena_affiliation.py \
        --name "NOCA University" \
        --country-code BR \
        --subdivision-code BR-SP \
        --url "https://example.edu" \
        --image /path/to/logo.png

Optional clear flags can be used to null nullable fields on update.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from secrets_manager import SecretsConfig, SecretsManager
from sqlalchemy import select
from sqlalchemy.pool import NullPool

from arena.config import settings as arena_settings
from arena.database import create_engine, create_session_factory
from arena.models.arena_affiliations import ArenaAffiliation
from arena.services.profile_location_service import validate_country_code, validate_subdivision_code
from shared.app_logging import configure_logging
from shared.db_schema.custom_types import init_encrypted_string
from shared.services.imageprocessing_service.validation import image_validation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImagePayload:
    """Image bytes encoded for persistence in an affiliation record."""

    logo_base64: str
    logo_mime: str


def _utcnow() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(UTC)


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Create or update an Arena affiliation.")
    parser.add_argument("--name", required=True, help="Affiliation unique display name")
    parser.add_argument("--country-code", help="ISO 3166-1 alpha-2 country code")
    parser.add_argument("--subdivision-code", help="ISO 3166-2 subdivision code")
    parser.add_argument("--url", help="Affiliation public URL")
    parser.add_argument("--image", help="Local image path to store as base64 logo")
    parser.add_argument(
        "--clear-country-code",
        action="store_true",
        help="Clear the stored country code",
    )
    parser.add_argument(
        "--clear-subdivision-code",
        action="store_true",
        help="Clear the stored subdivision code",
    )
    parser.add_argument(
        "--clear-url",
        action="store_true",
        help="Clear the stored URL",
    )
    parser.add_argument(
        "--clear-image",
        action="store_true",
        help="Clear the stored logo image and MIME type",
    )
    args = parser.parse_args()
    _validate_clear_flags(args)
    return args


def _validate_clear_flags(args: argparse.Namespace) -> None:
    """Reject mutually exclusive update/clear combinations."""
    if args.country_code and args.clear_country_code:
        raise ValueError("Use either --country-code or --clear-country-code, not both.")
    if args.subdivision_code and args.clear_subdivision_code:
        raise ValueError("Use either --subdivision-code or --clear-subdivision-code, not both.")
    if args.url and args.clear_url:
        raise ValueError("Use either --url or --clear-url, not both.")
    if args.image and args.clear_image:
        raise ValueError("Use either --image or --clear-image, not both.")


def _load_image_payload(image_path: str) -> ImagePayload:
    """Read an image file, validate it, and return base64 plus detected MIME type."""
    path = Path(image_path)
    if not path.is_file():
        raise ValueError(f"Image file not found: {path}")
    content = path.read_bytes()
    metadata = image_validation(content=content, supported_formats={"JPEG", "PNG", "WEBP"})
    return ImagePayload(
        logo_base64=base64.b64encode(content).decode("ascii"),
        logo_mime=metadata.mime_type,
    )


def _normalize_optional_text(value: str | None) -> str | None:
    """Return a stripped string or ``None`` when empty."""
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


async def upsert_affiliation(args: argparse.Namespace) -> None:
    """Create or update an affiliation identified by its unique name."""
    load_dotenv(arena_settings.ARENA_CRYPTO_ENV_FILE, override=False)
    secrets_config = SecretsConfig.from_environment()
    secrets_manager = SecretsManager(secrets_config)
    init_encrypted_string(secrets_manager)

    engine = create_engine(arena_settings.db_url, poolclass=NullPool)
    session_factory = create_session_factory(engine)

    try:
        async with session_factory() as session:
            now = _utcnow()
            normalized_name = args.name.strip()
            if not normalized_name:
                raise ValueError("Affiliation name cannot be empty.")

            affiliation = await session.scalar(select(ArenaAffiliation).where(ArenaAffiliation.name == normalized_name))
            created = affiliation is None

            if affiliation is None:
                affiliation = ArenaAffiliation()
                affiliation.id = str(uuid.uuid4())
                affiliation.name = normalized_name
                affiliation.created_at = now
                session.add(affiliation)

            normalized_country = _resolve_country_code(args, affiliation.country_code)
            normalized_subdivision = _resolve_subdivision_code(args, normalized_country, affiliation.subdivision_code)
            image_payload = _load_image_payload(args.image) if args.image else None

            affiliation.name = normalized_name
            affiliation.country_code = normalized_country
            affiliation.subdivision_code = normalized_subdivision

            if args.clear_url:
                affiliation.url = None
            elif args.url is not None:
                affiliation.url = _normalize_optional_text(args.url)

            if args.clear_image:
                affiliation.logo_base64 = None
                affiliation.logo_mime = None
            elif image_payload is not None:
                affiliation.logo_base64 = image_payload.logo_base64
                affiliation.logo_mime = image_payload.logo_mime

            affiliation.updated_at = now
            await session.commit()

            action = "created" if created else "updated"
            print(f"SUCCESS: Arena affiliation {action}")
            print(f"  id:                {affiliation.id}")
            print(f"  name:              {affiliation.name}")
            print(f"  country_code:      {affiliation.country_code}")
            print(f"  subdivision_code:  {affiliation.subdivision_code}")
            print(f"  url:               {affiliation.url}")
            print(f"  logo_mime:         {affiliation.logo_mime}")
            print(f"  has_logo:          {affiliation.logo_base64 is not None}")
    finally:
        await engine.dispose()


def _resolve_country_code(args: argparse.Namespace, existing_country_code: str | None) -> str | None:
    """Resolve the final country code considering update and clear flags."""
    if args.clear_country_code:
        return None
    if args.country_code is not None:
        return validate_country_code(args.country_code)
    return existing_country_code


def _resolve_subdivision_code(
    args: argparse.Namespace,
    country_code: str | None,
    existing_subdivision_code: str | None,
) -> str | None:
    """Resolve the final subdivision code considering update and clear flags."""
    if args.clear_country_code or args.clear_subdivision_code:
        return None
    if args.subdivision_code is not None:
        return validate_subdivision_code(country_code, args.subdivision_code)
    if args.country_code is not None:
        return validate_subdivision_code(country_code, existing_subdivision_code)
    return existing_subdivision_code


if __name__ == "__main__":
    configure_logging(logging_level=logging.INFO)
    try:
        asyncio.run(upsert_affiliation(_parse_args()))
    except ValueError as exc:
        logger.error(str(exc))
        sys.exit(1)
    sys.exit(0)
