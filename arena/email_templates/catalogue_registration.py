#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena registration, consent, and signup-report email definitions."""

from __future__ import annotations

from pathlib import Path

from shared.services.email_templates import EmailTemplateDefinition

_DEFAULTS = Path(__file__).with_name("defaults")

REGISTRATION_EMAIL_TEMPLATE_DEFINITIONS = (
    EmailTemplateDefinition(
        key="reset_password",
        default_path=_DEFAULTS / "reset_password.toml",
        subject_placeholders=frozenset(),
        body_placeholders=frozenset({"nome", "url"}),
        required_placeholders=frozenset({"url"}),
        sample_values={"nome": "Ada Lovelace", "url": "https://arena.example/auth/password-reset?token=example"},
    ),
    EmailTemplateDefinition(
        key="confirm_your_email",
        default_path=_DEFAULTS / "confirm_your_email.toml",
        subject_placeholders=frozenset(),
        body_placeholders=frozenset({"nome", "url"}),
        required_placeholders=frozenset({"url"}),
        sample_values={"nome": "Ada Lovelace", "url": "https://arena.example/auth/activate?token=example"},
    ),
    EmailTemplateDefinition(
        key="parental_consent",
        default_path=_DEFAULTS / "parental_consent.toml",
        subject_placeholders=frozenset(),
        body_placeholders=frozenset({"nome", "url"}),
        required_placeholders=frozenset({"url"}),
        sample_values={
            "nome": "Young Programmer",
            "url": "https://arena.example/auth/parental-consent?token=example",
        },
    ),
    EmailTemplateDefinition(
        key="account_activated",
        default_path=_DEFAULTS / "account_activated.toml",
        subject_placeholders=frozenset(),
        body_placeholders=frozenset({"nome"}),
        required_placeholders=frozenset(),
        sample_values={"nome": "Ada Lovelace"},
    ),
    EmailTemplateDefinition(
        key="account_already_exists",
        default_path=_DEFAULTS / "account_already_exists.toml",
        subject_placeholders=frozenset(),
        body_placeholders=frozenset({"login_url", "reset_url"}),
        required_placeholders=frozenset({"login_url", "reset_url"}),
        sample_values={
            "login_url": "https://arena.example/auth/login",
            "reset_url": "https://arena.example/auth/password-reset",
        },
    ),
    EmailTemplateDefinition(
        key="parental_consent_confirmed",
        default_path=_DEFAULTS / "parental_consent_confirmed.toml",
        subject_placeholders=frozenset(),
        body_placeholders=frozenset({"nome", "revoke_url"}),
        required_placeholders=frozenset({"revoke_url"}),
        sample_values={
            "nome": "Young Programmer",
            "revoke_url": "https://arena.example/auth/parental-consent/revoke?token=example",
        },
    ),
    EmailTemplateDefinition(
        key="parental_consent_revoked",
        default_path=_DEFAULTS / "parental_consent_revoked.toml",
        subject_placeholders=frozenset(),
        body_placeholders=frozenset({"nome"}),
        required_placeholders=frozenset(),
        sample_values={"nome": "Young Programmer"},
    ),
    EmailTemplateDefinition(
        key="new_user_reputation",
        default_path=_DEFAULTS / "new_user_reputation.toml",
        subject_placeholders=frozenset({"nome"}),
        body_placeholders=frozenset(
            {
                "nome",
                "email",
                "user_id",
                "signup_ip",
                "ip_reputation_section",
                "email_reputation_section",
            }
        ),
        required_placeholders=frozenset(),
        sample_values={
            "nome": "Ada Lovelace",
            "email": "ada@example.com",
            "user_id": "00000000-0000-0000-0000-000000000001",
            "signup_ip": "192.0.2.10",
            "ip_reputation_section": "  Fraud score:  5/100\n  Proxy:        no",
            "email_reputation_section": "  Fraud score:   3/100\n  Overall score: 4/4",
        },
    ),
)
