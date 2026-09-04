#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""ORM model for Arena users.

This module owns the mapping itself -- the column declarations, the
relationships, and the few properties derived from more than one of them.
Behavior that wraps a single concern is carried by mixins so that this file
stays a readable description of the table:

- :class:`~arena.models.arena_user_credentials.ArenaUserCredentialsMixin` --
  email, password, OTP secret, AI API key, JWT token identity
- :class:`~arena.models.arena_user_media.ArenaUserMediaMixin` -- profile photo,
  avatar, and the deterministic SVG fallback
- :class:`~arena.models.arena_user_age.ArenaUserAgeMixin` -- age computation
  and formatting
- :class:`~arena.models.mixins.LocationMixin` -- country and subdivision display
"""

from __future__ import annotations

from datetime import date, datetime
from math import exp
from typing import TYPE_CHECKING

from sqlalchemy.orm import Mapped, relationship

from arena.database import ArenaBase
from arena.models.arena_user_age import ArenaUserAgeMixin
from arena.models.arena_user_credentials import ArenaUserCredentialsMixin
from arena.models.arena_user_media import ArenaUserMediaMixin
from arena.models.mixins import LocationMixin
from shared.db_schema.arena import (
    arena_users as arena_users_table,
)
from shared.enumerations import ArenaRole
from shared.services.arena_rating import CONFIDENCE_SCALE

if TYPE_CHECKING:
    from arena.models.arena_affiliations import ArenaAffiliation
    from arena.models.arena_auth_records import ArenaBackup2FA, ArenaLoginHistory
    from arena.models.arena_badges import ArenaUserBadge
    from arena.models.arena_notifications import ArenaNotification
    from arena.models.arena_submissions import ArenaSubmission, ArenaUserSolvedProblem, ArenaUserTriedProblem
    from arena.models.arena_user_google_identity import ArenaUserGoogleIdentity
    from arena.models.arena_user_reputation import ArenaUserReputation


class ArenaUser(
    ArenaUserCredentialsMixin,
    ArenaUserMediaMixin,
    ArenaUserAgeMixin,
    LocationMixin,
    ArenaBase,
):
    """ORM model for an Arena platform user.

    Maps onto the `arena_users` Core table. Email, password, photo, OTP and age
    behavior lives in the mixins named in this module's docstring.

    Attributes:
        id: UUID string primary key.
        nome: Full display name.
        username: Globally unique lowercase handle; the public display name and avatar seed.
        full_name_public: Adult opt-in to show the full name instead of the username.
        dta_troca_username: Timestamp of the last username change; enforces the cooldown.
        consent_generation: Monotonic counter bumped on every parental-consent transition.
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
        public_profile: User opts in to a public profile page; requires ranking_visible=True.
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
        avatar_revision: Monotonic cache-busting revision for the effective avatar.
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
        public_display_name: Age-shielded public name (property; username
            unless the user is an adult who opted in to their legal name).
        effective_public_profile: Stored public_profile after the age shield
            and the ranking_visible coupling (property).
        is_age_shielded: Whether the age shield currently applies (property).
        username_cooldown_days: Days left before the handle may change again
            (property).
    """

    __table__ = arena_users_table

    id: Mapped[str]
    nome: Mapped[str]
    username: Mapped[str]
    full_name_public: Mapped[bool]
    dta_troca_username: Mapped[datetime | None]
    consent_generation: Mapped[int]
    dta_nascimento: Mapped[date | None]
    email_normalizado: Mapped[str]
    email_canonical: Mapped[str | None]
    email_responsavel_legal: Mapped[str | None]
    consentimento_responsavel: Mapped[bool]
    dta_consentimento_responsavel: Mapped[datetime | None]
    aceitou_termos_privacidade: Mapped[bool]
    dta_aceitacao_termos_privacidade: Mapped[datetime | None]
    password_hash: Mapped[str]
    role: Mapped[ArenaRole]
    can_edit: Mapped[bool]
    ranking_visible: Mapped[bool]
    public_profile: Mapped[bool]
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
    avatar_revision: Mapped[int]
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
    current_streak: Mapped[int]
    longest_streak: Mapped[int]
    last_ac_date: Mapped[date | None]
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
    badges: Mapped[list[ArenaUserBadge]] = relationship(
        "ArenaUserBadge",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="ArenaUserBadge.user_id",
    )
    reputation: Mapped[ArenaUserReputation | None] = relationship(
        "ArenaUserReputation",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        foreign_keys="ArenaUserReputation.user_id",
    )
    google_identity: Mapped[ArenaUserGoogleIdentity | None] = relationship(
        "ArenaUserGoogleIdentity",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        foreign_keys="ArenaUserGoogleIdentity.user_id",
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
    # public identity (age shield)
    # ------------------------------------------------------------------

    @property
    def public_display_name(self) -> str:
        """Return the name a public surface may render for this user.

        Delegates to ``arena.services.user_visibility_service`` so a template
        can never reach past the shield by reading ``nome`` directly.

        Returns:
            str: The legal name for an adult who opted in, otherwise the
            pseudonymous username.
        """
        from arena.services.user_visibility_service import display_identity_for_user

        return display_identity_for_user(self).display_name

    @property
    def effective_public_profile(self) -> bool:
        """Return whether this user's public profile page is actually visible.

        Returns:
            bool: The stored ``public_profile`` flag after the age shield and
            the ``ranking_visible`` coupling are applied.
        """
        from arena.services.user_visibility_service import display_identity_for_user

        return display_identity_for_user(self).public_profile

    @property
    def is_age_shielded(self) -> bool:
        """Return whether the age shield currently applies to this account.

        A template needs this to decide whether to render the public-profile and
        full-name opt-ins ``disabled`` with an explanation. Exposing it here
        rather than injecting it into a route's render context is the same
        bypass-proofing choice as :attr:`public_display_name`: a template added
        later inherits the shield instead of depending on some route remembering
        to pass it, and the two page contracts stay unchanged.

        It is an affordance, never the control. Every write path re-derives the
        same rule server-side, so a browser that ignores ``disabled`` is still
        refused.

        Returns:
            bool: True for 13-17 year-olds and for an account with no recorded
            date of birth, which fails closed.
        """
        from arena.services.user_visibility_service import is_shielded

        return is_shielded(self.dta_nascimento)

    @property
    def username_cooldown_days(self) -> int:
        """Return the whole days left before this user may change their handle.

        Returns:
            int: Days still to wait, or ``0`` when a change is available now.
        """
        from arena.services.username_service import username_cooldown_remaining

        return username_cooldown_remaining(self)

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
