#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Seed script — creates a new ArenaUser.

Usage:
    uv run python scripts/arena/seed_arena_user.py \\
        --fullname "Daniel Correa Lobato" \\
        --email daniel@lobato.org \\
        --enabled \\
        --email-confirmed

Flags:
    --password         Optional plain-text password. Generated when omitted.
    --dob              Date of birth in YYYY-MM-DD format. Defaults to 2000-01-01.
    --enabled          Set ativo=True and fill dta_ativacao_conta
    --email-confirmed  Set email_confirmado=True and fill dta_validacao_email
    --role             Arena role: ARENA_USER (default), ARENA_ADMIN, ARENA_JUDGE
"""

import argparse
import asyncio
import logging
import sys
import uuid
from datetime import UTC, date, datetime

from dotenv import load_dotenv
from secrets_manager import SecretsConfig, SecretsManager
from sqlalchemy import select
from sqlalchemy.pool import NullPool

from arena.config import settings as arena_settings
from arena.database import create_engine, create_session_factory
from arena.models.arena_users import ArenaUser
from shared.app_logging import configure_logging
from shared.db_schema.custom_types import init_encrypted_string
from shared.services.password_service import generate_diceware_password

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def create_arena_user(
    fullname: str,
    email: str,
    password: str | None,
    date_of_birth: date,
    enabled: bool,
    email_confirmed: bool,
    role: str,
) -> None:
    """Create an Arena user when the email is not already registered.

    Args:
        fullname: User display name.
        email: User email address.
        password: Optional plain-text password. A diceware password is generated
            when this value is ``None``.
        date_of_birth: User date of birth.
        enabled: Whether the account should be active immediately.
        email_confirmed: Whether the account email should be marked as confirmed.
        role: Arena role value.
    """
    load_dotenv(arena_settings.ARENA_CRYPTO_ENV_FILE, override=False)
    secrets_config = SecretsConfig.from_environment()
    secrets_manager = SecretsManager(secrets_config)
    init_encrypted_string(secrets_manager)

    engine = create_engine(arena_settings.db_url, poolclass=NullPool)
    session_factory = create_session_factory(engine)

    try:
        async with session_factory() as session:
            normalized_email = _normalize_email(email)
            existing = await session.scalar(select(ArenaUser).where(ArenaUser.email_normalizado == normalized_email))
            if existing:
                logger.warning("ArenaUser '%s' already exists — nothing to do.", email)
                return

            resolved_password = password or generate_diceware_password(arena_settings)
            password_was_generated = password is None

            now = _utcnow()
            user = ArenaUser()
            user.id = str(uuid.uuid4())
            user.nome = fullname
            user.dta_nascimento = date_of_birth
            user.role = role
            user.session_version = 1
            user.consentimento_responsavel = True
            user.dta_consentimento_responsavel = now
            user.usa_2fa = False
            user.com_foto = False
            user.precisa_trocar_senha = False
            user.created_at = now
            user.updated_at = now
            user.ativo = enabled
            user.dta_ativacao_conta = now if enabled else None
            user.email_confirmado = email_confirmed
            user.dta_validacao_email = now if email_confirmed else None

            user.email = email
            user.password = resolved_password

            session.add(user)
            await session.commit()

            print("SUCCESS: ArenaUser created")
            print(f"  Email:    {email}")
            print(f"  Fullname: {fullname}")
            print(f"  Role:     {role}")
            print(f"  ativo:    {user.ativo}")
            print(f"  email_confirmado: {user.email_confirmado}")
            print(f"  id:       {user.id}")
            if password_was_generated:
                print(f"  Password: {resolved_password}")
    finally:
        await engine.dispose()


def _normalize_email(email: str) -> str:
    from shared.services.email_validation import EmailValidationService

    return EmailValidationService.normalize(email)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a new ArenaUser.")
    parser.add_argument("--fullname", required=True, help="User's full display name")
    parser.add_argument("--email", required=True, help="User's email address")
    parser.add_argument("--password", help="User's plain-text password; generated when omitted")
    parser.add_argument(
        "--dob",
        default="2000-01-01",
        help="User's date of birth in YYYY-MM-DD format (default: 2000-01-01)",
    )
    parser.add_argument(
        "--enabled",
        action="store_true",
        help="Mark account as active (ativo=True)",
    )
    parser.add_argument(
        "--email-confirmed",
        action="store_true",
        help="Mark email as confirmed (email_confirmado=True)",
    )
    parser.add_argument(
        "--role",
        default="ARENA_USER",
        choices=["ARENA_USER", "ARENA_JUDGE"],
        help="Arena role (default: ARENA_USER)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    configure_logging(logging_level=logging.INFO)
    args = parse_args()

    asyncio.run(
        create_arena_user(
            fullname=args.fullname,
            email=args.email,
            password=args.password,
            date_of_birth=date.fromisoformat(args.dob),
            enabled=args.enabled,
            email_confirmed=args.email_confirmed,
            role=args.role,
        )
    )
    sys.exit(0)
