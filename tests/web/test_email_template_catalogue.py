#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contract tests for Web's packaged email template catalogue."""

from web.email_templates import web_email_templates


def test_every_web_packaged_email_renders_with_its_sample_context() -> None:
    """Keep all Web defaults synchronized with their declared contracts."""
    rendered = web_email_templates().render_samples(brand_name="NOCA")

    assert set(rendered) == {
        "animator_credential",
        "send_credentials",
        "send_uberadmin_credentials",
    }
    assert all(email.subject and email.body for email in rendered.values())
