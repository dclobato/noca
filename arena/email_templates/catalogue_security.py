#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena account-security and credit email template definitions."""

from __future__ import annotations

from pathlib import Path

from shared.services.email_templates import EmailTemplateDefinition

_DEFAULTS = Path(__file__).with_name("defaults")
_USER_SAMPLE = {"nome": "Ada Lovelace"}


def _user_notice(key: str) -> EmailTemplateDefinition:
    """Build a definition for a notice whose only module placeholder is ``nome``."""
    return EmailTemplateDefinition(
        key=key,
        default_path=_DEFAULTS / f"{key}.toml",
        subject_placeholders=frozenset(),
        body_placeholders=frozenset({"nome"}),
        required_placeholders=frozenset(),
        sample_values=_USER_SAMPLE,
    )


SECURITY_EMAIL_TEMPLATE_DEFINITIONS = (
    _user_notice("password_changed"),
    _user_notice("google_account_linked"),
    _user_notice("google_account_unlinked"),
    _user_notice("2fa_enabled"),
    _user_notice("2fa_disabled_self"),
    EmailTemplateDefinition(
        key="backup_code_used",
        default_path=_DEFAULTS / "backup_code_used.toml",
        subject_placeholders=frozenset(),
        body_placeholders=frozenset({"nome", "remaining"}),
        required_placeholders=frozenset(),
        sample_values={"nome": "Ada Lovelace", "remaining": "7"},
    ),
    _user_notice("admin_2fa_disabled"),
    _user_notice("admin_google_unlinked_password"),
    _user_notice("admin_google_unlinked_inactive"),
    EmailTemplateDefinition(
        key="admin_google_unlinked_reset",
        default_path=_DEFAULTS / "admin_google_unlinked_reset.toml",
        subject_placeholders=frozenset(),
        body_placeholders=frozenset({"nome", "password_reset_url"}),
        required_placeholders=frozenset({"password_reset_url"}),
        sample_values={
            "nome": "Ada Lovelace",
            "password_reset_url": "https://arena.example/auth/password-reset",
        },
    ),
    EmailTemplateDefinition(
        key="admin_google_unlinked_inactive_reset",
        default_path=_DEFAULTS / "admin_google_unlinked_inactive_reset.toml",
        subject_placeholders=frozenset(),
        body_placeholders=frozenset({"nome", "password_reset_url"}),
        required_placeholders=frozenset({"password_reset_url"}),
        sample_values={
            "nome": "Ada Lovelace",
            "password_reset_url": "https://arena.example/auth/password-reset",
        },
    ),
    _user_notice("admin_password_change_required"),
    EmailTemplateDefinition(
        key="ai_credits_topped_up",
        default_path=_DEFAULTS / "ai_credits_topped_up.toml",
        subject_placeholders=frozenset(),
        body_placeholders=frozenset({"nome", "quantity", "quantity_credit_noun", "balance", "balance_credit_noun"}),
        required_placeholders=frozenset(),
        sample_values={
            "nome": "Ada Lovelace",
            "quantity": "2",
            "quantity_credit_noun": "credits",
            "balance": "7",
            "balance_credit_noun": "credits",
        },
    ),
)
