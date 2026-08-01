#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Redaction of secrets that would otherwise reach log output.

Some third-party APIs take their credential as a *URL path segment* rather than
a header, so the key travels inside every request URL. Such a URL reaches logs
through two independent paths: the HTTP client logs the request line at DEBUG,
and our own error handlers log exception messages that embed the failed URL.
Both are covered here -- at the call site through :func:`redact_secrets` and at
the logging layer through :class:`SecretRedactingFilter`.

The filter rewrites the record's message and string arguments; it cannot reach
text that only exists once a handler formats a traceback, so exception messages
should still be redacted at the call site before being logged.
"""

from __future__ import annotations

import logging
import re

MASK = "********"

#: IPQualityScore embeds the API key as a path segment:
#: ``/api/json/ip/<key>/<ip>`` and ``/api/json/email/<key>/<email>``. The host is
#: optional so this matches both a full URL and the bare path urllib3 logs.
_URL_PATH_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (re.compile(r"(/api/json/(?:ip|email)/)[^/?\s\"']+"),)


def redact_secrets(text: str) -> str:
    """Mask credentials embedded in URLs found in a string.

    Args:
        text: Arbitrary text that may contain a secret-bearing URL.

    Returns:
        The text with every recognized credential replaced by ``MASK``.
    """
    for pattern in _URL_PATH_SECRET_PATTERNS:
        text = pattern.sub(rf"\1{MASK}", text)
    return text


class SecretRedactingFilter(logging.Filter):
    """Mask URL-embedded credentials in log records instead of dropping them.

    Attached to a logger, the filter runs before any handler sees the record --
    including handlers installed by other tools, such as pytest's log capture --
    so the secret never reaches an output stream or a captured report.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact the record in place and always keep it.

        Args:
            record: The record about to be handled.

        Returns:
            Always ``True``: this filter redacts, it never suppresses.
        """
        if isinstance(record.msg, str):
            record.msg = redact_secrets(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact_secrets(arg) if isinstance(arg, str) else arg for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {
                key: redact_secrets(value) if isinstance(value, str) else value for key, value in record.args.items()
            }
        return True
