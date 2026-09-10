#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena class-membership email template definitions."""

from __future__ import annotations

from pathlib import Path

from shared.services.email_templates import EmailTemplateDefinition

_DEFAULTS = Path(__file__).with_name("defaults")

CLASS_EMAIL_TEMPLATE_DEFINITIONS = (
    EmailTemplateDefinition(
        key="class_registration_request",
        default_path=_DEFAULTS / "class_registration_request.toml",
        subject_placeholders=frozenset({"class_name"}),
        body_placeholders=frozenset({"teacher_name", "student_name", "class_name", "members_url"}),
        required_placeholders=frozenset({"members_url"}),
        sample_values={
            "teacher_name": "Grace Hopper",
            "student_name": "Ada Lovelace",
            "class_name": "Algorithms 101",
            "members_url": "https://arena.example/classes/example/members",
        },
    ),
    EmailTemplateDefinition(
        key="class_registration_approved",
        default_path=_DEFAULTS / "class_registration_approved.toml",
        subject_placeholders=frozenset({"class_name"}),
        body_placeholders=frozenset({"student_name", "class_name", "class_url"}),
        required_placeholders=frozenset({"class_url"}),
        sample_values={
            "student_name": "Ada Lovelace",
            "class_name": "Algorithms 101",
            "class_url": "https://arena.example/classes/example",
        },
    ),
    EmailTemplateDefinition(
        key="class_registration_denied",
        default_path=_DEFAULTS / "class_registration_denied.toml",
        subject_placeholders=frozenset({"class_name"}),
        body_placeholders=frozenset({"student_name", "class_name", "denial_reason_line"}),
        required_placeholders=frozenset(),
        sample_values={
            "student_name": "Ada Lovelace",
            "class_name": "Algorithms 101",
            "denial_reason_line": "Reason: The class is full.",
        },
    ),
    EmailTemplateDefinition(
        key="class_membership_added",
        default_path=_DEFAULTS / "class_membership_added.toml",
        subject_placeholders=frozenset({"class_name"}),
        body_placeholders=frozenset({"student_name", "class_name", "class_url"}),
        required_placeholders=frozenset({"class_url"}),
        sample_values={
            "student_name": "Ada Lovelace",
            "class_name": "Algorithms 101",
            "class_url": "https://arena.example/classes/example",
        },
    ),
    EmailTemplateDefinition(
        key="class_membership_removed",
        default_path=_DEFAULTS / "class_membership_removed.toml",
        subject_placeholders=frozenset({"class_name"}),
        body_placeholders=frozenset({"student_name", "class_name"}),
        required_placeholders=frozenset(),
        sample_values={"student_name": "Ada Lovelace", "class_name": "Algorithms 101"},
    ),
)
