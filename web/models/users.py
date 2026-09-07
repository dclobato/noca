#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from base64 import b64decode
from datetime import datetime
from functools import lru_cache
from typing import TYPE_CHECKING

from deterministic_avatar import DeterministicAvatar
from sqlalchemy.orm import Mapped, relationship

from shared.db_schema import login_history as login_history_table
from shared.db_schema import uber_admins as uber_admins_table
from shared.db_schema import users as users_table
from shared.db_schema import users_media as users_media_table
from shared.enumerations import RoleEnum
from shared.services.email_validation import EmailValidationService
from web.database import Base
from web.models._base import _utcnow

if TYPE_CHECKING:
    from web.models.clarification import Clarification
    from web.models.contest import Contest, Task
    from web.models.site import Site
    from web.models.submission import HumanSubmissionConfirmation, Submission

_avatar_generator = DeterministicAvatar()


@lru_cache(maxsize=256)
def _generate_cached_avatar(username: str) -> bytes:
    return _avatar_generator.generate_avatar(seed=username, formal=True).encode("utf-8")


class BaseUser:
    __abstract__ = True

    @property
    def password(self) -> str:
        return self.password_hash

    @password.setter
    def password(self, value: str) -> None:
        from werkzeug.security import generate_password_hash

        self.password_hash = generate_password_hash(value)


class UberAdmin(Base, BaseUser):
    __table__ = uber_admins_table

    id: Mapped[str]
    username: Mapped[str]
    fullname: Mapped[str]
    password_hash: Mapped[str]
    email_normalizado: Mapped[str]
    is_enabled: Mapped[bool]
    created_by_uberadmin: Mapped[str | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    login_history: Mapped[list[Login_History]] = relationship(
        "Login_History",
        back_populates="uberadmin",
        foreign_keys="Login_History.uberadmin_id",
        cascade="all, delete-orphan",
    )

    @property
    def role(self) -> RoleEnum:
        return RoleEnum.UBERADMIN

    @property
    def email(self) -> str:
        return self.email_normalizado

    @email.setter
    def email(self, value: str) -> None:
        try:
            normalizado = EmailValidationService.normalize(value)
        except ValueError as e:
            raise ValueError(f"Invalid email address: {value}") from e
        self.email_normalizado = normalizado


class User(Base, BaseUser):
    __table__ = users_table

    id: Mapped[str]
    username: Mapped[str]
    email_normalizado: Mapped[str | None]
    fullname: Mapped[str]
    password_hash: Mapped[str]
    role: Mapped[RoleEnum]
    site_id: Mapped[str | None]
    location: Mapped[str | None]
    contest_id: Mapped[str]
    created_by_admin_id: Mapped[str | None]
    created_by_uberadmin_id: Mapped[str | None]
    allow_concurrent_login: Mapped[bool]
    session_epoch: Mapped[int]
    locked_ip: Mapped[str | None]
    locked_at: Mapped[datetime | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    contest: Mapped[Contest] = relationship(
        back_populates="members",
        foreign_keys=[users_table.c.contest_id],
    )
    site: Mapped[Site | None] = relationship(
        "Site",
        back_populates="users",
        foreign_keys=[users_table.c.site_id],
    )
    owned_contest: Mapped[Contest | None] = relationship(
        back_populates="owner",
        foreign_keys="Contest.owner_user_id",
        uselist=False,
    )
    login_history: Mapped[list[Login_History]] = relationship(
        "Login_History",
        back_populates="user",
        foreign_keys="Login_History.user_id",
        cascade="all, delete-orphan",
    )
    clarifications_as_team: Mapped[list[Clarification]] = relationship(
        back_populates="team",
        foreign_keys="[Clarification.team_id]",
    )
    tasks_as_team: Mapped[list[Task]] = relationship(
        back_populates="team",
        foreign_keys="[Task.team_id]",
    )
    tasks_as_staff: Mapped[list[Task]] = relationship(
        back_populates="staff",
        foreign_keys="[Task.staff_id]",
    )
    hidden_clarifications_as_judge: Mapped[list[Clarification]] = relationship(
        back_populates="hidden_by_judge",
        foreign_keys="[Clarification.hidden_by_judge_id]",
    )
    hidden_clarifications_as_admin: Mapped[list[Clarification]] = relationship(
        back_populates="hidden_by_admin",
        foreign_keys="[Clarification.hidden_by_admin_id]",
    )
    submissions: Mapped[list[Submission]] = relationship(
        back_populates="team",
        foreign_keys="[Submission.team_id]",
    )
    confirmations_as_judge: Mapped[list[HumanSubmissionConfirmation]] = relationship(
        back_populates="judge",
        foreign_keys="[HumanSubmissionConfirmation.judge_id]",
    )
    media: Mapped[UserMedia | None] = relationship(
        "UserMedia",
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="raise",
        passive_deletes=True,
    )

    @property
    def email(self) -> str | None:
        return self.email_normalizado

    @email.setter
    def email(self, value: str) -> None:
        try:
            normalizado = EmailValidationService.normalize(value)
        except ValueError as e:
            raise ValueError(f"Invalid email address: {value}") from e
        self.email_normalizado = normalizado

    @property
    def generated_avatar(self) -> tuple[bytes, str]:
        """Return the deterministic fallback avatar for this user."""
        return _generate_cached_avatar(self.username), "image/svg+xml"

    @property
    def media_cache_version(self) -> int:
        """Return a URL-safe cache version for user media."""
        return int(self.updated_at.timestamp() * 1_000_000)


class UserMedia(Base):
    """Stored photo and audio media for a contest user."""

    __table__ = users_media_table

    user_id: Mapped[str]
    com_foto: Mapped[bool]
    foto_base64: Mapped[str | None]
    avatar_base64: Mapped[str | None]
    foto_mime: Mapped[str | None]
    dta_foto: Mapped[datetime | None]
    audio_base64: Mapped[str | None]
    audio_mime: Mapped[str | None]
    dta_audio: Mapped[datetime | None]

    user: Mapped[User] = relationship("User", back_populates="media")

    @property
    def foto(self) -> tuple[bytes, str] | None:
        """Return the decoded full photo and MIME type when present."""
        if not self.com_foto or not self.foto_base64:
            return None
        return b64decode(self.foto_base64), self.foto_mime or "application/octet-stream"

    @property
    def avatar(self) -> tuple[bytes, str] | None:
        """Return the decoded avatar and MIME type when present."""
        if not self.com_foto or not self.avatar_base64:
            return None
        return b64decode(self.avatar_base64), self.foto_mime or "application/octet-stream"

    @property
    def audio(self) -> tuple[bytes, str] | None:
        """Return the decoded audio clip and MIME type when present."""
        if not self.audio_base64 or not self.audio_mime:
            return None
        return b64decode(self.audio_base64), self.audio_mime

    @property
    def photo_cache_version(self) -> int:
        """Return a URL-safe cache version for the stored photo."""
        if self.dta_foto is None:
            return 0
        return int(self.dta_foto.timestamp() * 1_000_000)

    @property
    def audio_cache_version(self) -> int:
        """Return a URL-safe cache version for the stored audio clip."""
        if self.dta_audio is None:
            return 0
        return int(self.dta_audio.timestamp() * 1_000_000)

    def apply_processed_photo(
        self,
        *,
        foto_base64: str,
        avatar_base64: str,
        mime_type: str,
    ) -> None:
        """Store a processed photo and its derived avatar."""
        if not foto_base64 or not avatar_base64 or not mime_type:
            raise ValueError("foto_base64, avatar_base64 and mime_type are required to apply a processed photo")
        self.foto_base64 = foto_base64
        self.avatar_base64 = avatar_base64
        self.foto_mime = mime_type
        self.dta_foto = _utcnow()
        self.com_foto = True

    def clear_foto_fields(self) -> None:
        """Clear the stored photo and avatar fields."""
        self.com_foto = False
        self.foto_base64 = None
        self.avatar_base64 = None
        self.foto_mime = None
        self.dta_foto = None

    def apply_audio(self, *, audio_base64: str, mime_type: str) -> None:
        """Store a validated audio clip."""
        if not audio_base64 or not mime_type:
            raise ValueError("audio_base64 and mime_type are required to apply audio")
        self.audio_base64 = audio_base64
        self.audio_mime = mime_type
        self.dta_audio = _utcnow()

    def clear_audio_fields(self) -> None:
        """Clear the stored audio clip fields."""
        self.audio_base64 = None
        self.audio_mime = None
        self.dta_audio = None


class Login_History(Base):
    """Modelo para armazenar histórico de logins dos usuários."""

    __table__ = login_history_table

    id: Mapped[int]
    user_id: Mapped[str | None]
    uberadmin_id: Mapped[str | None]
    dta_login: Mapped[datetime]
    ip_address: Mapped[str | None]
    source_port: Mapped[int | None]
    country_code: Mapped[str | None]
    subdivision_code: Mapped[str | None]
    district: Mapped[str | None]
    city: Mapped[str | None]
    is_eu: Mapped[bool | None]
    as_number: Mapped[str | None]
    user_agent: Mapped[str | None]

    user: Mapped[User | None] = relationship(
        "User",
        back_populates="login_history",
        foreign_keys=[login_history_table.c.user_id],
    )
    uberadmin: Mapped[UberAdmin | None] = relationship(
        "UberAdmin",
        back_populates="login_history",
        foreign_keys=[login_history_table.c.uberadmin_id],
    )
