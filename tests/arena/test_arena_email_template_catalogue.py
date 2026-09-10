#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contract and caller tests for Arena's packaged email templates."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from arena.email_templates import arena_email_templates
from arena.services.signup_reputation_service import (
    _render_email_reputation_section,
    _render_ip_reputation_section,
)
from arena.services.user_security_notification_service import (
    send_admin_google_unlinked_email,
    send_ai_credits_topped_up_email,
)
from shared.services.email_reputation import EmailReputation
from shared.services.network_utils.ip_reputation import IPReputation


def test_every_arena_packaged_email_renders_with_its_sample_context() -> None:
    """Keep all Arena defaults synchronized with their declared contracts."""
    rendered = arena_email_templates().render_samples(brand_name="NOCA")

    assert len(rendered) == 26
    assert all(email.subject and email.body for email in rendered.values())


@pytest.mark.parametrize(
    ("password_reset_url", "inactive", "expected", "unexpected"),
    (
        ("https://arena.example/reset", False, "reset email will be sent", "currently deactivated"),
        (None, False, "keep signing in", "currently deactivated"),
        (None, True, "currently deactivated", "setting a password"),
        ("https://arena.example/reset", True, "setting a password does not reactivate", "keep signing in"),
    ),
)
async def test_admin_google_unlink_selects_all_four_complete_variants(
    password_reset_url: str | None,
    inactive: bool,
    expected: str,
    unexpected: str,
) -> None:
    """Select the full unlink message in Python from both independent inputs."""
    user = MagicMock(id="user-1", nome="Ada", email="ada@example.test")
    service = MagicMock(send_email=AsyncMock(return_value=MagicMock(success=True)))

    await send_admin_google_unlinked_email(
        user,
        service,
        admin_id="admin-1",
        password_reset_url=password_reset_url,
        account_inactive=inactive,
    )

    body = service.send_email.call_args.kwargs["text_body"]
    assert expected in body
    assert unexpected not in body


@pytest.mark.parametrize(
    ("quantity", "balance", "expected"),
    ((1, 1, "1 AI review credit have been added"), (2, 1, "2 AI review credits have been added")),
)
async def test_credit_nouns_are_inflected_before_rendering(quantity: int, balance: int, expected: str) -> None:
    """Supply display-ready singular and plural credit nouns from Python."""
    user = MagicMock(id="user-1", nome="Ada", email="ada@example.test")
    service = MagicMock(send_email=AsyncMock(return_value=MagicMock(success=True)))

    await send_ai_credits_topped_up_email(user, service, quantity, balance, admin_id="admin-1")

    body = service.send_email.call_args.kwargs["text_body"]
    assert expected in body
    assert f"balance is {balance} {'credit' if balance == 1 else 'credits'}" in body


def test_reputation_sections_are_built_as_display_ready_text() -> None:
    """Move optional reputation-object branching completely out of templates."""
    ip_reputation = IPReputation(
        is_crawler=False,
        mobile=True,
        recent_abuse=True,
        fraud_score=88,
        proxy=True,
        vpn=True,
        tor=False,
        active_vpn=True,
        active_tor=False,
    )
    email_reputation = EmailReputation(
        valid=True,
        disposable=True,
        suspect=False,
        overall_score=3,
        common=False,
        fraud_score=42,
        sanitized_email="ada@example.test",
    )

    assert "Fraud score:  88/100" in _render_ip_reputation_section(ip_reputation)
    assert "VPN:          yes (active: yes)" in _render_ip_reputation_section(ip_reputation)
    assert "Overall score: 3/4" in _render_email_reputation_section(email_reputation)
    assert _render_ip_reputation_section(None) == "  Not available."
    assert _render_email_reputation_section(None) == "  Not available."
