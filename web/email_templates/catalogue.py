#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Web's catalogue of stable email template keys and their contracts."""

from __future__ import annotations

from pathlib import Path

from shared.services.email_templates import EmailTemplateDefinition

_DEFAULTS = Path(__file__).with_name("defaults")

WEB_EMAIL_TEMPLATE_DEFINITIONS = (
    EmailTemplateDefinition(
        key="animator_credential",
        default_path=_DEFAULTS / "animator_credential.toml",
        subject_placeholders=frozenset({"contest_name"}),
        body_placeholders=frozenset({"fullname", "contest_name", "label", "scope_label", "token", "sender_name"}),
        required_placeholders=frozenset({"token"}),
        sample_values={
            "fullname": "Ada Lovelace",
            "contest_name": "NOCA Invitational",
            "label": "Main projector",
            "scope_label": "Global",
            "token": "noca_example_operator_token",
            "sender_name": "NOCA Operations",
        },
    ),
    EmailTemplateDefinition(
        key="send_credentials",
        default_path=_DEFAULTS / "send_credentials.toml",
        subject_placeholders=frozenset({"contest_name"}),
        body_placeholders=frozenset(
            {
                "fullname",
                "contest_name",
                "contest_login_url",
                "username",
                "password",
                "sender_name",
            }
        ),
        required_placeholders=frozenset({"contest_login_url", "username", "password"}),
        sample_values={
            "fullname": "Ada Lovelace",
            "contest_name": "NOCA Invitational",
            "contest_login_url": "https://contest.example/login",
            "username": "ada",
            "password": "example-password",
            "sender_name": "NOCA Operations",
        },
    ),
    EmailTemplateDefinition(
        key="send_uberadmin_credentials",
        default_path=_DEFAULTS / "send_uberadmin_credentials.toml",
        subject_placeholders=frozenset(),
        body_placeholders=frozenset({"fullname", "login_url", "username", "password", "sender_name"}),
        required_placeholders=frozenset({"login_url", "username", "password"}),
        sample_values={
            "fullname": "Ada Lovelace",
            "login_url": "https://noca.example/uberadmin/login",
            "username": "ada@example.com",
            "password": "example-password",
            "sender_name": "NOCA Operations",
        },
    ),
)
