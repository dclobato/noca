#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Bootstrap script — creates the initial Arena admin user.

Usage:
    uv run python scripts/arena/create_arena_admin.py
    uv run python scripts/arena/create_arena_admin.py --email admin@example.com --fullname "Admin Name"

Idempotent: if a user with the given email already exists, the script exits cleanly.
"""

import argparse
import asyncio
import logging
import sys
import uuid
from datetime import UTC, datetime

from dotenv import load_dotenv
from secrets_manager import SecretsConfig, SecretsManager
from sqlalchemy import select
from sqlalchemy.pool import NullPool

from arena.config import settings
from arena.database import create_engine, create_session_factory
from arena.models.arena_users import ArenaUser
from shared.app_logging import configure_logging
from shared.db_schema.custom_types import init_encrypted_string
from shared.enumerations import ArenaRole

logger = logging.getLogger(__name__)

DEFAULT_FULLNAME = settings.ARENA_ADMIN_FULLNAME or "Arena Administrator"
DEFAULT_EMAIL = settings.ARENA_ADMIN_EMAIL or ""
DEFAULT_PASSWORD = settings.ARENA_ADMIN_PASSWORD or ""


async def create_arena_admin(fullname: str, email: str, password: str) -> None:
    """Create an Arena user with ARENA_ADMIN role when the email is not already registered.

    Args:
        fullname: User display name.
        email: User email address.
        password: Plain-text password.
    """
    load_dotenv(settings.ARENA_CRYPTO_ENV_FILE, override=False)
    secrets_config = SecretsConfig.from_environment()
    secrets_manager = SecretsManager(secrets_config)
    init_encrypted_string(secrets_manager)

    engine = create_engine(settings.db_url, poolclass=NullPool)
    session_factory = create_session_factory(engine)

    try:
        async with session_factory() as session:
            from shared.services.email_validation import EmailValidationService

            normalized_email = EmailValidationService.normalize(email)
            existing = await session.scalar(select(ArenaUser).where(ArenaUser.email_normalizado == normalized_email))
            if existing:
                logger.warning("ArenaUser '%s' already exists — nothing to do.", email)
                return

            now = datetime.now(UTC)
            user = ArenaUser()
            user.id = str(uuid.uuid4())
            user.nome = fullname
            user.dta_nascimento = now.date().replace(year=now.year - 30)
            user.role = ArenaRole.ARENA_ADMIN
            user.session_version = 1
            user.consentimento_responsavel = True
            user.dta_consentimento_responsavel = now
            user.usa_2fa = False
            user.com_foto = False
            user.precisa_trocar_senha = False
            user.created_at = now
            user.updated_at = now
            user.ativo = True
            user.dta_ativacao_conta = now
            user.email_confirmado = True
            user.dta_validacao_email = now

            user.email = email
            user.password = password

            session.add(user)
            await session.commit()
            logger.info("Arena admin '%s' created successfully.", email)
    finally:
        await engine.dispose()


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Create the initial Arena admin user.")
    parser.add_argument("--fullname", default=DEFAULT_FULLNAME)
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    return parser.parse_args()


if __name__ == "__main__":
    configure_logging()
    args = parse_args()
    if not args.email:
        print("ERROR: email is required (set NOCA_ARENA_ADMIN_EMAIL or pass --email)", file=sys.stderr)
        sys.exit(1)
    if not args.password:
        print("ERROR: password is required (set NOCA_ARENA_ADMIN_PASSWORD or pass --password)", file=sys.stderr)
        sys.exit(1)
    asyncio.run(create_arena_admin(fullname=args.fullname, email=args.email, password=args.password))
    sys.exit(0)
