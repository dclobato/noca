#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import Mapped, relationship

from shared.db_schema import sites as sites_table
from web.database import Base

if TYPE_CHECKING:
    from web.models.contest import Contest
    from web.models.users import User


class Site(Base):
    __table__ = sites_table

    id: Mapped[str]
    sitename: Mapped[str]
    sitename_normalized: Mapped[str]
    contest_id: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    contest: Mapped[Contest] = relationship(
        "Contest",
        back_populates="sites",
        foreign_keys=[sites_table.c.contest_id],
    )
    users: Mapped[list[User]] = relationship(
        "User",
        back_populates="site",
        foreign_keys="User.site_id",
    )
