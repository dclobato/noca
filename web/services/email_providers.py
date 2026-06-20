#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Compatibility re-exports for shared email providers."""

from shared.services.email_providers import EmailProvider, EmailProviderError, MockProvider, SMTPProvider

__all__ = ["EmailProvider", "EmailProviderError", "MockProvider", "SMTPProvider"]
