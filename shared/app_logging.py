#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import asyncio
import logging

from pydantic import BaseModel

from shared.log_redaction import MASK as _MASK
from shared.log_redaction import SecretRedactingFilter

# Name tokens (split on "_") that mark a settings field as secret-bearing.  Only
# applied to non-empty string values, so policy fields such as PASSWORD_WORD_COUNT
# (int) or PASSWORD_UPPERCASE_REQUIRED (bool) are never masked.
_SENSITIVE_NAME_TOKENS = frozenset({"PASSWORD", "PASSWD", "PWD", "SECRET", "KEY", "TOKEN"})

# Loggers whose records may carry a credential-bearing URL. Filtering at the
# emitting logger (rather than only on our handler) also covers records that
# other tools capture, such as pytest's log capture.
_SECRET_BEARING_LOGGERS = ("urllib3", "requests", "httpx", "httpcore")


def sqlalchemy_echo_enabled(logging_level: int) -> bool:
    """Return whether SQLAlchemy statement echo is enabled for a log level."""
    return logging_level == logging.DEBUG


def _is_sensitive_field(name: str, value: object) -> bool:
    """Return True when a settings field should be masked in logs.

    A field is sensitive when any underscore-delimited token of its name is a known
    secret token AND the value is a non-empty string.  The string guard keeps numeric
    policy fields visible and the token approach keeps VALKEY_* visible (the token
    'VALKEY' is not 'KEY').
    """
    if not isinstance(value, str) or not value:
        return False
    return any(token in _SENSITIVE_NAME_TOKENS for token in name.upper().split("_"))


def log_settings(logger: logging.Logger, settings: BaseModel, *, level: int = logging.DEBUG) -> None:
    """Log every resolved settings value, marking defaults vs. env overrides.

    Dumps each field of a pydantic settings model on its own line, masking
    secret-bearing string fields (passwords, API keys, JWT secrets).  Each line is
    tagged ``[override]`` when the field was set from the environment/.env file or
    ``[default]`` when it kept its declared default.  Computed ``@property`` values
    (e.g. ``db_url``) are intentionally excluded since they embed secrets.

    Emitted at DEBUG level by default, so the dump stays silent when a module runs
    with a higher effective log level (e.g. INFO in production).
    """
    fields_set = settings.model_fields_set
    logger.log(level, "- Resolved configuration values:")
    for name in sorted(settings.model_dump().keys()):
        value = getattr(settings, name)
        display = _MASK if _is_sensitive_field(name, value) else value
        origin = "override" if name in fields_set else "default"
        logger.log(level, "    %s = %s [%s]", name, display, origin)


class _SuppressCancelledErrorFilter(logging.Filter):
    """Suppress ASGI-application error records caused by CancelledError.

    During graceful shutdown uvicorn cancels in-flight SSE connections once the
    ``timeout_graceful_shutdown`` window expires.  The resulting CancelledError
    propagates through Starlette's middleware and is logged by uvicorn at ERROR
    level as "Exception in ASGI application".  These cancellations are expected
    and not actionable, so we drop them here to keep the shutdown output clean.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Return False (suppress) when the record carries a CancelledError."""
        if record.exc_info:
            exc_type = record.exc_info[0]
            if exc_type is not None and issubclass(exc_type, asyncio.CancelledError):
                return False
        return True


def _install_secret_redaction(console_handler: logging.Handler) -> None:
    """Mask URL-embedded credentials on our handler and at noisy HTTP loggers.

    HTTP clients log the full request line at DEBUG, and APIs that take their key
    as a URL path segment therefore leak it into any DEBUG-level output. The
    handler filter covers everything we print; the per-logger filters also cover
    records captured by handlers we do not own.
    """
    console_handler.addFilter(SecretRedactingFilter())
    for logger_name in _SECRET_BEARING_LOGGERS:
        logger = logging.getLogger(logger_name)
        # Idempotent: configure_logging may run more than once per process (tests,
        # hot reload), and filters would otherwise stack on the same logger.
        if not any(isinstance(existing, SecretRedactingFilter) for existing in logger.filters):
            logger.addFilter(SecretRedactingFilter())


def configure_logging(logging_level: int = logging.DEBUG) -> None:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging_level)
    console_handler.setFormatter(MainConsoleFormatter())
    _install_secret_redaction(console_handler)

    # Configurar o root logger para a aplicação
    root_logger = logging.getLogger()
    root_logger.setLevel(logging_level)
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)

    # Suppress expected CancelledError noise from uvicorn during graceful shutdown.
    # log_config=None keeps our logging setup intact, so the filter persists.
    logging.getLogger("uvicorn.error").addFilter(_SuppressCancelledErrorFilter())


class MainConsoleFormatter(logging.Formatter):
    GREY = "\x1b[90m"  # Bright black (dark gray)
    GREEN = "\x1b[32m"  # Green
    YELLOW = "\x1b[33m"  # Yellow
    RED = "\x1b[31m"  # Red
    RESET = "\x1b[0m"  # Reset
    # FORMAT = "%(asctime)s | %(levelname)-7s |  %(filename)-10s:%(lineno)4d, %(funcName)-10s | %(message)s"
    FORMAT = "%(asctime)s | %(levelname)-7s | %(message)s (@%(filename)s:%(lineno)d, %(funcName)s)"

    FORMATS = {
        logging.DEBUG: GREY + FORMAT + RESET,
        logging.INFO: GREEN + FORMAT + RESET,
        logging.WARNING: YELLOW + FORMAT + RESET,
        logging.ERROR: RED + FORMAT + RESET,
        logging.CRITICAL: RED + FORMAT + RESET,
    }

    def format(self, record: logging.LogRecord) -> str:
        log_fmt = type(self).FORMATS.get(record.levelno, type(self).GREY + type(self).FORMAT + type(self).RESET)
        formatter = logging.Formatter(log_fmt, datefmt="%d %H:%M:%S")
        # noinspection StrFormat
        return formatter.format(record)
