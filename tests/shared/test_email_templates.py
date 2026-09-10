#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the constrained shared email-template contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from shared.services.email_templates import (
    BODY_MAX_BYTES,
    SUBJECT_MAX_CHARACTERS,
    EmailTemplate,
    EmailTemplateDefinition,
    EmailTemplateError,
    EmailTemplateRegistry,
    validate_email_template,
)


def _definition(path: Path) -> EmailTemplateDefinition:
    """Return a compact definition used by renderer unit tests."""
    return EmailTemplateDefinition(
        key="security_notice",
        default_path=path,
        subject_placeholders=frozenset({"name"}),
        body_placeholders=frozenset({"name", "url"}),
        required_placeholders=frozenset({"url"}),
        sample_values={"name": "Ada", "url": "https://example.test/action"},
    )


def _write_default(path: Path) -> None:
    """Write a valid default template for a temporary registry."""
    path.write_text(
        'subject = "Notice for {name} from {brand_name}"\nbody = "Open {url}, {name}."\n',
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "invalid_body",
    ("Hello {Name}", "Hello {{name}}", "Hello {name!r}", "Hello {name", "Hello name}"),
)
def test_rejects_every_brace_outside_the_exact_grammar(tmp_path: Path, invalid_body: str) -> None:
    """Reject uppercase, expression, doubled, and unbalanced brace forms."""
    definition = _definition(tmp_path / "default.toml")
    with pytest.raises(EmailTemplateError, match="malformed placeholder"):
        validate_email_template(EmailTemplate(subject="Notice", body=invalid_body), definition)


def test_rejects_unknown_and_missing_required_placeholders(tmp_path: Path) -> None:
    """Enforce both sides of a catalogue's placeholder contract."""
    definition = _definition(tmp_path / "default.toml")
    with pytest.raises(EmailTemplateError, match="unknown placeholders: intruder"):
        validate_email_template(EmailTemplate(subject="Notice", body="{url} {intruder}"), definition)
    with pytest.raises(EmailTemplateError, match="missing required placeholders: url"):
        validate_email_template(EmailTemplate(subject="Notice", body="Hello {name}"), definition)


@pytest.mark.parametrize(
    ("template", "message"),
    (
        (EmailTemplate(subject="line one\nline two", body="{url}"), "single line"),
        (EmailTemplate(subject="x" * (SUBJECT_MAX_CHARACTERS + 1), body="{url}"), "subject exceeds"),
        (EmailTemplate(subject="Notice", body="é" * (BODY_MAX_BYTES // 2 + 1) + "{url}"), "body exceeds"),
    ),
)
def test_rejects_subject_injection_and_size_limit_violations(
    tmp_path: Path,
    template: EmailTemplate,
    message: str,
) -> None:
    """Apply the shared single-line and output-size limits."""
    with pytest.raises(EmailTemplateError, match=message):
        validate_email_template(template, _definition(tmp_path / "default.toml"))


def test_render_substitutes_once_and_injects_brand_name(tmp_path: Path) -> None:
    """Treat inserted brace text as data instead of recursively evaluating it."""
    path = tmp_path / "default.toml"
    _write_default(path)
    registry = EmailTemplateRegistry((_definition(path),))

    rendered = registry.render(
        "security_notice",
        brand_name="NOCA",
        context={"name": "{url}", "url": "https://example.test/action"},
    )

    assert rendered.subject == "Notice for {url} from NOCA"
    assert rendered.body == "Open https://example.test/action, {url}."


def test_render_rejects_missing_context_and_rendered_subject_newline(tmp_path: Path) -> None:
    """Validate caller context and the final subject after substitution."""
    path = tmp_path / "default.toml"
    _write_default(path)
    registry = EmailTemplateRegistry((_definition(path),))
    with pytest.raises(EmailTemplateError, match="render context is missing 'url'"):
        registry.render("security_notice", brand_name="NOCA", context={"name": "Ada"})
    with pytest.raises(EmailTemplateError, match="single line"):
        registry.render(
            "security_notice",
            brand_name="NOCA",
            context={"name": "Ada\nBcc: victim@example.test", "url": "https://example.test"},
        )


@dataclass
class _StaticOverride:
    """Return one fixed override through the backend-independent lookup contract."""

    template: EmailTemplate | None

    def get(self, definition: EmailTemplateDefinition) -> EmailTemplate | None:
        """Return the configured override regardless of key."""
        return self.template


def test_override_lookup_precedes_packaged_default_and_can_fall_back(tmp_path: Path) -> None:
    """Use a valid override when present and the packaged template otherwise."""
    path = tmp_path / "default.toml"
    _write_default(path)
    definition = _definition(path)
    override = EmailTemplate(subject="Override for {name}", body="Use {url}")
    overridden = EmailTemplateRegistry((definition,), override_lookup=_StaticOverride(override))
    fallback = EmailTemplateRegistry((definition,), override_lookup=_StaticOverride(None))
    context = {"name": "Ada", "url": "https://example.test"}

    assert overridden.render("security_notice", brand_name="NOCA", context=context).subject == "Override for Ada"
    assert fallback.render("security_notice", brand_name="NOCA", context=context).subject == "Notice for Ada from NOCA"
