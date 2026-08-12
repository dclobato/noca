#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Neutral health-monitor HTTP and backend exception responses."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from shared.error_handlers import (
    BackendErrorConfig,
    create_backend_error_handlers,
    register_backend_error_handlers,
    register_generic_error_handlers,
)

logger = logging.getLogger(__name__)

_backend_config = BackendErrorConfig(
    logger=logger,
    templates_state_attr="templates",
    template_name="errors/backend.html",
    unavailable_heading="The uptime dashboard is temporarily unavailable",
)
_backend_handlers = create_backend_error_handlers(_backend_config)
database_exception_handler = _backend_handlers.database
unexpected_exception_handler = _backend_handlers.unexpected


def register_error_handlers(app: FastAPI) -> None:
    """Register all health-monitor HTTP and backend exception handlers."""
    register_backend_error_handlers(app, _backend_handlers)
    register_generic_error_handlers(app, _backend_config)
