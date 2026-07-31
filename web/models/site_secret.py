#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from datetime import datetime

from sqlalchemy.orm import Mapped

from shared.db_schema import site_secrets as site_secrets_table
from web.database import Base


class SiteSecret(Base):
    """Operator authorization secret for the animator reveal engine.

    A row with ``site_id`` set authorizes control of that site; a row with
    ``site_id`` NULL is a contest-global control secret. Only the fixed-length
    ``secret_digest`` is persisted; the plaintext token exists solely in the
    return value of the create operation.
    """

    __table__ = site_secrets_table

    id: Mapped[str]
    contest_id: Mapped[str]
    site_id: Mapped[str | None]
    secret_digest: Mapped[str]
    label: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
