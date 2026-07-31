#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The audit boundary that guarantees one record per control request.

Why a route class and not per-decision logging
----------------------------------------------

Most control refusals never reach the route function that could log them: the
contest gate and kill switch answer inside a dependency, an invalid credential
answers in the authorization dependency, and a malformed body is rejected by
FastAPI's request validation before any dependency-consuming code runs. An
``APIRoute`` subclass wraps the *whole* per-request pipeline — dependency
solving, body validation, and the handler — so it is the one place all of those
outcomes pass through.

Why not middleware
------------------

Middleware runs before routing, so it would also wrap non-control traffic and
could not tell a control operation from any other request; scoping it by URL
prefix would duplicate the router's own matching. A ``route_class`` is applied by
the router itself, to exactly its own operations.

What is *not* audited: a request that never matches a control operation — a
misspelled sub-path, or a wrong method on a real one (Starlette answers ``405``
from the router, before any route handler). No control operation was attempted
in either case.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from animator.services.control_audit import emit_control_audit

__all__ = ["ControlAuditRoute"]

_INTERNAL_ERROR_STATUS = 500


class ControlAuditRoute(APIRoute):
    """An ``APIRoute`` that emits exactly one audit record per request."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """Wrap the standard handler so every exit path is audited once.

        Returns:
            The audited request handler.
        """
        handle = super().get_route_handler()

        async def audited(request: Request) -> Response:
            """Run the operation, emit its single audit record, and re-raise."""
            try:
                response = await handle(request)
            except HTTPException as exc:
                # Gate 404s, credential 403s, and every mapped domain refusal.
                emit_control_audit(request, status=exc.status_code)
                raise
            except RequestValidationError:
                # FastAPI's own body/path validation, e.g. a smuggled site_id.
                emit_control_audit(request, status=422)
                raise
            except Exception:
                # An unexpected failure is still an attempt, and the one worth
                # noticing in the audit stream; the handler's own traceback is
                # logged by the ASGI server.
                emit_control_audit(request, status=_INTERNAL_ERROR_STATUS)
                raise
            emit_control_audit(request, status=response.status_code)
            return response

        return audited
