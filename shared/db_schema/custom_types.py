#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Custom SQLAlchemy TypeDecorators for the NOCA schema.

Provides EncryptedString, a TypeDecorator that transparently encrypts column
values at write time and decrypts them at read time using a SecretsManager
instance.  The SecretsManager must be initialised once at application startup
by calling init_encrypted_string().
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from secrets_manager import SecretsManager
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

_secrets_manager: SecretsManager | None = None

_VERSION_SEPARATOR = ":"
_VERSION_PREFIX = "v"


def init_encrypted_string(sm: SecretsManager) -> None:
    """Register the application SecretsManager for EncryptedString columns.

    Args:
        sm: Initialised SecretsManager instance.  Must be called once during
            application startup before any ORM reads or writes on columns
            declared with EncryptedString.
    """
    global _secrets_manager
    _secrets_manager = sm


def _get_sm() -> SecretsManager:
    """Return the registered SecretsManager, raising if not yet initialised.

    Returns:
        SecretsManager: The registered instance.

    Raises:
        RuntimeError: If init_encrypted_string() has not been called yet.
    """
    if _secrets_manager is None:
        raise RuntimeError(
            "EncryptedString: SecretsManager has not been initialised. "
            "Call shared.db_schema.custom_types.init_encrypted_string() "
            "during application startup before performing any DB operations "
            "on encrypted columns."
        )
    return _secrets_manager


def _is_encrypted(value: str) -> bool:
    """Return True if value looks like an already-encrypted token.

    Args:
        value: String to inspect.

    Returns:
        bool: True if the value matches the 'vN:base64' format.
    """
    if not isinstance(value, str):
        return False
    parts = value.split(_VERSION_SEPARATOR, 1)
    return len(parts) == 2 and parts[0].startswith(_VERSION_PREFIX)


class EncryptedString(TypeDecorator[str]):
    """SQLAlchemy column type that encrypts values at rest.

    Uses the globally-registered SecretsManager (set via init_encrypted_string)
    to encrypt on write and decrypt on read.  The stored format is::

        "<version>:<base64url-encoded-ciphertext>"

    Example::

        class MyModel(Base):
            secret: Mapped[str | None] = mapped_column(EncryptedString(length=500))

    Attributes:
        impl: Underlying SQL type used in the database schema.
        cache_ok: Permits SQLAlchemy to cache compiled expressions for this type.
    """

    impl = String
    cache_ok = True

    def __init__(self, length: int = 500, **kwargs: Any) -> None:
        """Initialise the type with the column storage length.

        Args:
            length: Maximum length of the encrypted token stored in the DB.
                    Must be large enough for the version prefix, separator,
                    and base64-encoded Fernet ciphertext.
            **kwargs: Forwarded to the underlying String type.
        """
        super().__init__(length=length, **kwargs)

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        """Encrypt a plaintext value before writing to the database.

        Args:
            value: Plaintext string to encrypt, or None.
            dialect: SQLAlchemy dialect (unused).

        Returns:
            str | None: Encrypted token string, or None if value is None.

        Raises:
            TypeError: If encryption fails unexpectedly.
        """
        if value is None:
            return None
        if isinstance(value, str) and _is_encrypted(value):
            return value
        try:
            sm = _get_sm()
            version, ciphertext = sm.encrypt(str(value).encode("utf-8"))
            b64 = base64.urlsafe_b64encode(ciphertext).decode("ascii")
            return f"{version}{_VERSION_SEPARATOR}{b64}"
        except RuntimeError:
            raise
        except Exception as exc:
            raise TypeError("EncryptedString: failed to encrypt value for storage.") from exc

    def process_result_value(self, value: Any, dialect: Any) -> str | None:
        """Decrypt a stored value when reading from the database.

        Args:
            value: Encrypted token string from the DB, or None.
            dialect: SQLAlchemy dialect (unused).

        Returns:
            str | None: Decrypted plaintext, or None if value is None.

        Raises:
            ValueError: If decryption fails or the stored token is malformed.
        """
        if value is None:
            return None
        try:
            sm = _get_sm()
            if _VERSION_SEPARATOR in value:
                version_hint, b64 = value.split(_VERSION_SEPARATOR, 1)
                ciphertext = base64.urlsafe_b64decode(b64.encode("ascii"))
            else:
                version_hint = None
                ciphertext = base64.urlsafe_b64decode(value.encode("ascii"))
            _, plaintext = sm.decrypt(ciphertext, version_hint)
            return str(plaintext.decode("utf-8"))
        except RuntimeError:
            raise
        except (ValueError, binascii.Error) as exc:
            raise ValueError("EncryptedString: stored value is not valid base64.") from exc
        except Exception as exc:
            raise ValueError("EncryptedString: failed to decrypt value from database.") from exc
