#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""ORM model for Arena users."""

from __future__ import annotations

from base64 import b64decode
from datetime import UTC, date, datetime
from math import exp
from typing import TYPE_CHECKING

from dateutil.relativedelta import relativedelta
from deterministic_avatar import DeterministicAvatar
from sqlalchemy.orm import Mapped, relationship

from arena.database import ArenaBase
from arena.models.mixins import LocationMixin
from shared.db_schema.arena import (
    arena_users as arena_users_table,
)
from shared.enumerations import ArenaRole
from shared.services.arena_rating import CONFIDENCE_SCALE

if TYPE_CHECKING:
    from arena.models.arena_affiliations import ArenaAffiliation
    from arena.models.arena_auth_records import ArenaBackup2FA, ArenaLoginHistory
    from arena.models.arena_notifications import ArenaNotification
    from arena.models.arena_submissions import ArenaSubmission, ArenaUserSolvedProblem, ArenaUserTriedProblem

_avatar_generator = DeterministicAvatar()


def _utcnow() -> datetime:
    """Return current UTC datetime.

    Returns:
        datetime: Current UTC time as timezone-aware datetime.
    """
    return datetime.now(UTC)


class ArenaUser(LocationMixin, ArenaBase):
    """ORM model for an Arena platform user.

    Maps onto the `arena_users` Core table and exposes email/password/photo/
    OTP setters and a collection of read-only computed properties.

    Attributes:
        id: UUID string primary key.
        nome: Full display name.
        dta_nascimento: Date of birth (optional).
        email_normalizado: Unique normalised email; the login identifier.
        email_responsavel_legal: Parent or legal guardian email for minors.
        consentimento_responsavel: Parent or legal guardian consent is confirmed.
        dta_consentimento_responsavel: Timestamp of parental consent.
        aceitou_termos_privacidade: User accepted the Terms of Service and Privacy Policy.
        dta_aceitacao_termos_privacidade: Timestamp when ToS and Privacy Policy were accepted.
        password_hash: Werkzeug password hash.
        role: ArenaRole enum value.
        can_edit: User may add/edit problems on the Arena problem base (admins always may).
        ranking_visible: User consents to appear in the public ranking; rating is still computed when False.
        ativo: Account is enabled and may log in.
        dta_ativacao_conta: Timestamp of account activation.
        email_confirmado: Email address has been verified.
        dta_validacao_email: Timestamp of email verification.
        dta_ultima_alteracao_senha: Timestamp of last password change.
        ultimo_login: Timestamp of last successful login.
        com_foto: True when a profile photo is stored.
        foto_base64: Original photo as base64 string.
        avatar_base64: Resized avatar as base64 string.
        foto_mime: MIME type of the stored photo.
        dta_foto: Timestamp of last photo upload.
        usa_2fa: TOTP 2FA is active.
        dta_mudanca_2fa: Timestamp of last 2FA status change.
        _otp_secret: Encrypted TOTP secret (access via otp_secret property).
        ultimo_otp: Last used OTP code.
        ai_backend_credits: AI credits for backend API Key
        _ai_api_key: Encrypted AI provider API key (access via ai_api_key property).
        precisa_trocar_senha: Forced password-change flag.
        dta_marcacao_troca_senha: Timestamp when forced change was set.
        session_version: Incremented on password change; invalidates old JWTs.
        codigos_otp: Relationship to ArenaBackup2FA backup codes.
        login_history: Relationship to ArenaLoginHistory login records.
        prefered_language: User interface and AI-response language locale.
    """

    __table__ = arena_users_table

    id: Mapped[str]
    nome: Mapped[str]
    dta_nascimento: Mapped[date | None]
    email_normalizado: Mapped[str]
    email_responsavel_legal: Mapped[str | None]
    consentimento_responsavel: Mapped[bool]
    dta_consentimento_responsavel: Mapped[datetime | None]
    aceitou_termos_privacidade: Mapped[bool]
    dta_aceitacao_termos_privacidade: Mapped[datetime | None]
    password_hash: Mapped[str]
    role: Mapped[ArenaRole]
    can_edit: Mapped[bool]
    ranking_visible: Mapped[bool]
    ativo: Mapped[bool]
    dta_ativacao_conta: Mapped[datetime | None]
    email_confirmado: Mapped[bool]
    dta_validacao_email: Mapped[datetime | None]
    dta_ultima_alteracao_senha: Mapped[datetime | None]
    ultimo_login: Mapped[datetime | None]
    com_foto: Mapped[bool]
    foto_base64: Mapped[str | None]
    avatar_base64: Mapped[str | None]
    foto_mime: Mapped[str | None]
    dta_foto: Mapped[datetime | None]
    usa_2fa: Mapped[bool]
    dta_mudanca_2fa: Mapped[datetime | None]
    _otp_secret: Mapped[str | None]
    ultimo_otp: Mapped[str | None]
    ai_backend_credits: Mapped[int]
    _ai_api_key: Mapped[str | None]
    precisa_trocar_senha: Mapped[bool]
    dta_marcacao_troca_senha: Mapped[datetime | None]
    session_version: Mapped[int]
    dta_rating_update: Mapped[datetime | None]
    user_rating: Mapped[int | None]
    solved_problems: Mapped[int | None]
    country_code: Mapped[str | None]
    subdivision_code: Mapped[str | None]
    affiliation_id: Mapped[str | None]
    preferred_language_id: Mapped[str | None]
    prefered_language: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    submissions: Mapped[list[ArenaSubmission]] = relationship(
        "ArenaSubmission",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="ArenaSubmission.user_id",
    )
    solved_problems_records: Mapped[list[ArenaUserSolvedProblem]] = relationship(
        "ArenaUserSolvedProblem",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="ArenaUserSolvedProblem.user_id",
    )
    tried_problems_records: Mapped[list[ArenaUserTriedProblem]] = relationship(
        "ArenaUserTriedProblem",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="ArenaUserTriedProblem.user_id",
    )
    notifications: Mapped[list[ArenaNotification]] = relationship(
        "ArenaNotification",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="ArenaNotification.user_id",
    )

    codigos_otp: Mapped[list[ArenaBackup2FA]] = relationship(
        "ArenaBackup2FA",
        back_populates="arena_user",
        cascade="all, delete-orphan",
        foreign_keys="ArenaBackup2FA.arena_user_id",
    )
    login_history: Mapped[list[ArenaLoginHistory]] = relationship(
        "ArenaLoginHistory",
        back_populates="arena_user",
        cascade="all, delete-orphan",
        foreign_keys="ArenaLoginHistory.arena_user_id",
    )
    affiliation: Mapped[ArenaAffiliation | None] = relationship(
        "ArenaAffiliation",
        back_populates="users",
        foreign_keys=[arena_users_table.c.affiliation_id],
    )

    # ------------------------------------------------------------------
    # email getter/setter
    # ------------------------------------------------------------------

    @property
    def email(self) -> str:
        """Return the normalised email address.

        Returns:
            str: Normalised email.
        """
        return self.email_normalizado

    @email.setter
    def email(self, value: str) -> None:
        """Normalise and store the email address.

        Args:
            value: Raw email address to normalise.

        Raises:
            ValueError: If the email address is invalid.
        """
        from shared.services.email_validation import EmailValidationService

        try:
            self.email_normalizado = EmailValidationService.normalize(value)
        except ValueError as exc:
            raise ValueError(f"Invalid email address: {value}") from exc

    # ------------------------------------------------------------------
    # email_mascarado
    # ------------------------------------------------------------------

    @property
    def email_mascarado(self) -> str:
        """Return a masked email suitable for display (e.g. use***@ex****.com).

        Returns:
            str: Masked email address.
        """
        from shared.services.email_validation import EmailValidationService

        return EmailValidationService.mask(self.email_normalizado)

    # ------------------------------------------------------------------
    # password getter/setter
    # ------------------------------------------------------------------

    @property
    def password(self) -> str:
        """Return the stored password hash.

        Returns:
            str: Werkzeug password hash.
        """
        return self.password_hash

    @password.setter
    def password(self, value: str) -> None:
        """Hash and store a new password, updating related security fields.

        Sets dta_ultima_alteracao_senha, clears precisa_trocar_senha, and
        increments session_version to invalidate all existing JWT tokens.

        Args:
            value: Plaintext password.
        """
        from werkzeug.security import generate_password_hash

        self.password_hash = generate_password_hash(value)
        self.dta_ultima_alteracao_senha = _utcnow()
        self.precisa_trocar_senha = False
        self.dta_marcacao_troca_senha = None
        current = self.session_version if self.session_version is not None else 0
        self.session_version = (current + 1) % 65536

    def check_password(self, password: str) -> bool:
        """Verify a plaintext password against the stored hash.

        Args:
            password: Plaintext password to verify.

        Returns:
            bool: True if the password matches.
        """
        from werkzeug.security import check_password_hash

        return check_password_hash(str(self.password_hash), password)

    # ------------------------------------------------------------------
    # otp_secret getter/setter
    # ------------------------------------------------------------------

    @property
    def otp_secret(self) -> str | None:
        """Return the decrypted OTP secret.

        Returns:
            str | None: Decrypted TOTP secret, or None if not set.
        """
        return self._otp_secret

    @otp_secret.setter
    def otp_secret(self, value: str | None) -> None:
        """Store the OTP secret (encrypted at rest by EncryptedString).

        Args:
            value: Plaintext TOTP secret, or None to clear.
        """
        self._otp_secret = value

    # ------------------------------------------------------------------
    # ai_api_key getter/setter
    # ------------------------------------------------------------------

    @property
    def ai_api_key(self) -> str | None:
        """Return the decrypted AI provider API key.

        Returns:
            str | None: Decrypted API key, or None if not set.
        """
        return self._ai_api_key

    @ai_api_key.setter
    def ai_api_key(self, value: str | None) -> None:
        """Store the AI provider API key (encrypted at rest by EncryptedString).

        Args:
            value: Plaintext API key, or None to clear.
        """
        self._ai_api_key = value

    # ------------------------------------------------------------------
    # is_active
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """Return True when account, email, and consent gates are clear.

        Returns:
            bool: True if the user may log in.
        """
        return self.ativo and self.email_confirmado and self.consentimento_responsavel

    # ------------------------------------------------------------------
    # foto / avatar
    # ------------------------------------------------------------------

    @property
    def foto(self) -> tuple[bytes, str]:
        """Return the profile photo as raw bytes and its MIME type.

        Falls back to a deterministic SVG avatar when no photo is stored.

        Returns:
            tuple[bytes, str]: Photo bytes and MIME type.
        """
        if self.com_foto:
            return b64decode(str(self.foto_base64)), self.foto_mime or "application/octet-stream"
        data = _avatar_generator.generate_avatar(seed=self.email_normalizado, formal=True).encode("utf-8")
        return data, "image/svg+xml"

    @property
    def avatar(self) -> tuple[bytes, str]:
        """Return the avatar as raw bytes and its MIME type.

        Falls back to the same deterministic SVG avatar as foto when no photo
        is stored.

        Returns:
            tuple[bytes, str]: Avatar bytes and MIME type.
        """
        if self.com_foto:
            return b64decode(str(self.avatar_base64)), self.foto_mime or "application/octet-stream"
        data = _avatar_generator.generate_avatar(seed=self.email_normalizado, formal=True).encode("utf-8")
        return data, "image/svg+xml"

    def apply_processed_photo(
        self,
        *,
        foto_base64: str,
        avatar_base64: str,
        mime_type: str,
    ) -> None:
        """Store pre-processed photo data directly.

        Use this when the photo has already been processed by an external
        image-processing service.

        Args:
            foto_base64: Base64-encoded full-size photo.
            avatar_base64: Base64-encoded resized avatar.
            mime_type: MIME type of the photo.

        Raises:
            ValueError: If any argument is empty or None.
        """
        if not foto_base64 or not avatar_base64 or not mime_type:
            raise ValueError("foto_base64, avatar_base64, and mime_type are all required")
        self.foto_base64 = foto_base64
        self.avatar_base64 = avatar_base64
        self.foto_mime = mime_type
        self.dta_foto = _utcnow()
        self.com_foto = True

    def clear_foto_fields(self) -> None:
        """Clear all photo-related fields without committing.

        Sets com_foto=False and nulls foto_base64, avatar_base64, foto_mime,
        and dta_foto.
        """
        self.com_foto = False
        self.foto_base64 = None
        self.avatar_base64 = None
        self.foto_mime = None
        self.dta_foto = None

    # ------------------------------------------------------------------
    # idade / idade_str
    # ------------------------------------------------------------------

    def idade(
        self,
        meses: bool = False,
        dias: bool = False,
        data_referencia: date | None = None,
    ) -> dict[str, int] | None:
        """Calculate the user's age with optional month and day granularity.

        Args:
            meses: Include months in the returned dict.
            dias: Include days in the returned dict (implies meses=True).
            data_referencia: Reference date for the calculation; defaults to
                today in UTC.

        Returns:
            dict[str, int] | None: Dict with keys 'anos', optionally 'meses'
                and 'dias'. None if dta_nascimento is not set.
        """
        if not isinstance(self.dta_nascimento, date):
            return None
        ref = data_referencia or datetime.now(UTC).date()
        incluir_meses = meses or dias
        diff = relativedelta(ref, self.dta_nascimento)
        resultado: dict[str, int] = {"anos": diff.years}
        if incluir_meses:
            resultado["meses"] = diff.months
        if dias:
            resultado["dias"] = diff.days
        return resultado

    def idade_str(
        self,
        meses: bool = False,
        dias: bool = False,
        data_referencia: date | None = None,
    ) -> str | None:
        """Return a human-readable Portuguese string for the user's age.

        Args:
            meses: Include months when age >= 1 year.
            dias: Include days (implies meses=True).
            data_referencia: Reference date; defaults to today in UTC.

        Returns:
            str | None: e.g. "25 anos", "1 ano e 3 meses", or None.
        """
        ref = data_referencia or datetime.now(UTC).date()
        incluir_meses = meses or dias
        idade = self.idade(meses=incluir_meses, dias=dias, data_referencia=ref)
        if not idade:
            return None
        anos = idade.get("anos", 0)
        meses_ = idade.get("meses", 0)
        dias_ = idade.get("dias", 0)
        partes: list[str] = []
        if anos > 0:
            partes.append(f"{anos} ano{'s' if anos != 1 else ''}")
            if incluir_meses and meses_:
                partes.append(f"{meses_} {'meses' if meses_ != 1 else 'mês'}")
            if dias and dias_:
                partes.append(f"{dias_} dia{'s' if dias_ != 1 else ''}")
        elif meses_ > 0:
            partes.append(f"{meses_} {'meses' if meses_ != 1 else 'mês'}")
            if dias and dias_:
                partes.append(f"{dias_} dia{'s' if dias_ != 1 else ''}")
        else:
            partes.append(f"{dias_} dia{'s' if dias_ != 1 else ''}")
        if len(partes) > 1:
            return ", ".join(partes[:-1]) + " e " + partes[-1]
        return partes[0]

    # ------------------------------------------------------------------
    # token identity
    # ------------------------------------------------------------------

    def get_token_id(self) -> str:
        """Return a compound token identity string for JWT subject claims.

        Combines the user ID, a suffix of the password hash, and the current
        session_version. Any JWT carrying an older session_version (after a
        password change) is automatically invalid.

        Returns:
            str: "{id}|{last_15_chars_of_hash}|{session_version}"
        """
        return f"{self.id}|{self.password_hash[-15:]}|{self.session_version}"

    # ------------------------------------------------------------------
    # rating stuff
    # ------------------------------------------------------------------
    @property
    def user_rating_confidence(self) -> int:
        if not self.solved_problems or self.solved_problems == 0:
            return 0
        return round(100 * (1 - exp(-self.solved_problems / CONFIDENCE_SCALE)))

    @property
    def affiliation_name(self) -> str | None:
        """Return the selected affiliation display name."""
        return self.affiliation.name if self.affiliation is not None else None
