#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for URL-embedded secret redaction in log output."""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest

from shared.app_logging import _SECRET_BEARING_LOGGERS, configure_logging
from shared.log_redaction import MASK, SecretRedactingFilter, redact_secrets

_KEY = "yo6b1oLQuLU4oGF7Xqm2hAPRgQZBKFFH"


def test_redacts_key_from_full_ip_url() -> None:
    """The API key path segment of an IP lookup URL is masked."""
    url = f"https://www.ipqualityscore.com/api/json/ip/{_KEY}/137.131.144.150?strictness=1"

    redacted = redact_secrets(url)

    assert _KEY not in redacted
    assert redacted.endswith(f"/api/json/ip/{MASK}/137.131.144.150?strictness=1")


def test_redacts_key_from_full_email_url() -> None:
    """The email endpoint carries its key the same way and is masked too."""
    redacted = redact_secrets(f"https://www.ipqualityscore.com/api/json/email/{_KEY}/user%40example.com")

    assert _KEY not in redacted
    assert f"/api/json/email/{MASK}/user%40example.com" in redacted


def test_redacts_key_from_bare_request_path() -> None:
    """urllib3 logs only the request path, which must be masked as well."""
    line = f"GET /api/json/ip/{_KEY}/1.2.3.4?strictness=1 HTTP/1.1"

    redacted = redact_secrets(line)

    assert _KEY not in redacted
    assert redacted == f"GET /api/json/ip/{MASK}/1.2.3.4?strictness=1 HTTP/1.1"


def test_leaves_unrelated_text_untouched() -> None:
    """Text without a credential-bearing URL is returned unchanged."""
    text = "Failed to get IP reputation for 1.2.3.4: connection refused"

    assert redact_secrets(text) == text


@pytest.mark.parametrize(
    "message, args",
    [
        (f"requesting https://www.ipqualityscore.com/api/json/ip/{_KEY}/1.2.3.4", ()),
        ("requesting %s", (f"https://www.ipqualityscore.com/api/json/ip/{_KEY}/1.2.3.4",)),
        # A mapping must be wrapped in a tuple; LogRecord unwraps it into record.args.
        ("%(url)s failed", ({"url": f"https://www.ipqualityscore.com/api/json/ip/{_KEY}/1.2.3.4"},)),
    ],
)
def test_filter_masks_message_and_arguments(message: str, args: object) -> None:
    """The filter redacts the message, positional args, and mapping args alike."""
    record = logging.LogRecord("test", logging.DEBUG, __file__, 1, message, args, None)  # type: ignore[arg-type]

    assert SecretRedactingFilter().filter(record) is True
    assert _KEY not in record.getMessage()
    assert MASK in record.getMessage()


def test_filter_keeps_non_string_arguments() -> None:
    """Non-string arguments survive redaction unchanged."""
    record = logging.LogRecord("test", logging.DEBUG, __file__, 1, "%s %s", ("plain", 42), None)

    assert SecretRedactingFilter().filter(record) is True
    assert record.getMessage() == "plain 42"


@pytest.fixture
def restored_logging() -> Iterator[None]:
    """Undo the global logging changes configure_logging performs."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_filters = {name: list(logging.getLogger(name).filters) for name in _SECRET_BEARING_LOGGERS}
    try:
        yield
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)
        for name, filters in saved_filters.items():
            logging.getLogger(name).filters = filters


def test_configure_logging_installs_filter_on_http_loggers(restored_logging: None) -> None:
    """A urllib3 request line never reaches handlers with the key intact."""
    configure_logging(logging.DEBUG)
    captured: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    handler = _Capture()
    logging.getLogger().addHandler(handler)
    try:
        logging.getLogger("urllib3.connectionpool").debug(
            '%s "%s %s %s" %s',
            "https://host:443",
            "GET",
            f"/api/json/ip/{_KEY}/1.2.3.4",
            "HTTP/1.1",
            200,
        )
    finally:
        logging.getLogger().removeHandler(handler)

    assert captured
    assert all(_KEY not in message for message in captured)
    assert any(MASK in message for message in captured)


def test_configure_logging_does_not_stack_filters(restored_logging: None) -> None:
    """Repeated configuration keeps exactly one redaction filter per logger."""
    configure_logging(logging.DEBUG)
    configure_logging(logging.DEBUG)

    for name in _SECRET_BEARING_LOGGERS:
        installed = [f for f in logging.getLogger(name).filters if isinstance(f, SecretRedactingFilter)]
        assert len(installed) == 1
