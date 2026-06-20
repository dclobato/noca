#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Compatibility re-exports for shared email models."""

from shared.services.email_models import EmailMessage, EmailResult, build_rfc5322_address

__all__ = ["EmailMessage", "EmailResult", "build_rfc5322_address"]
