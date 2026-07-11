#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""ORM models for Arena authentication records."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import Mapped, relationship

from arena.database import ArenaBase
from arena.models.mixins import LocationMixin
from shared.db_schema.arena import arena_backup_2fa as arena_backup_2fa_table
from shared.db_schema.arena import arena_login_history as arena_login_history_table

if TYPE_CHECKING:
    from arena.models.arena_users import ArenaUser


class ArenaBackup2FA(ArenaBase):
    """Single-use backup code for 2FA account recovery.

    Attributes:
        id: UUID string primary key.
        arena_user_id: FK to arena_users.
        hash_codigo: Werkzeug hash of the one-time backup code.
        utilizado: True when the code has been consumed.
        dta_uso: Timestamp of consumption.
        dta_para_remocao: Scheduled deletion timestamp.
        created_at: Record creation timestamp.
        arena_user: Back-reference to the owning ArenaUser.
    """

    __table__ = arena_backup_2fa_table

    id: Mapped[str]
    arena_user_id: Mapped[str]
    hash_codigo: Mapped[str]
    utilizado: Mapped[bool]
    dta_uso: Mapped[datetime | None]
    dta_para_remocao: Mapped[datetime | None]
    created_at: Mapped[datetime]

    arena_user: Mapped[ArenaUser] = relationship(
        "ArenaUser",
        back_populates="codigos_otp",
        foreign_keys=[arena_backup_2fa_table.c.arena_user_id],
    )

    def check_code(self, code: str) -> bool:
        """Verify a plaintext backup code against the stored hash.

        Args:
            code: Plaintext backup code to verify.

        Returns:
            bool: True if the code matches and has not been used.
        """
        from werkzeug.security import check_password_hash

        return not self.utilizado and check_password_hash(self.hash_codigo, code)


class ArenaLoginHistory(LocationMixin, ArenaBase):
    """Immutable record of a single Arena login event.

    Attributes:
        id: Sequential integer primary key.
        arena_user_id: FK to arena_users.
        dta_login: Timestamp of the login event.
        ip_address: Client IP address.
        country_code: ISO 3166-1 alpha-2 country code resolved from the IP.
        subdivision_code: ISO 3166-2 subdivision code resolved from the IP.
        district: District/county name resolved from the IP.
        city: City name resolved from the IP.
        is_eu: Whether the IP country is part of the EU.
        as_number: Autonomous System number resolved from the IP.
        user_agent: HTTP User-Agent header value.
        mode: Authentication method ('password', '2fa', 'backup_code').
        arena_user: Back-reference to the owning ArenaUser.
    """

    __table__ = arena_login_history_table

    id: Mapped[int]
    arena_user_id: Mapped[str]
    dta_login: Mapped[datetime]
    ip_address: Mapped[str | None]
    country_code: Mapped[str | None]
    subdivision_code: Mapped[str | None]
    district: Mapped[str | None]
    city: Mapped[str | None]
    is_eu: Mapped[bool | None]
    as_number: Mapped[str | None]
    user_agent: Mapped[str | None]
    mode: Mapped[str | None]

    arena_user: Mapped[ArenaUser] = relationship(
        "ArenaUser",
        back_populates="login_history",
        foreign_keys=[arena_login_history_table.c.arena_user_id],
    )

    @property
    def detailed_location(self) -> str | None:
        """Return a flag-less display string composed from the structured fields.

        Format: ``"<country>, <subdivision>, <city>, <district>"`` with any
        missing parts skipped. Returns None when no location fields are set.
        """
        parts = [part for part in (self.country_name, self.subdivision_name, self.city, self.district) if part]
        return ", ".join(parts) or None
