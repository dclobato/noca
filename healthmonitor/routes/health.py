#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Health monitor runtime health endpoint."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from healthmonitor.dependencies import enforce_healthmon_health_rate_limit

router = APIRouter(tags=["health"])


@router.get("/health", name="healthmon_health", dependencies=[Depends(enforce_healthmon_health_rate_limit)])
async def health(request: Request) -> JSONResponse:
    """Return the monitor's own runtime health."""
    valkey_runtime = getattr(request.app.state, "valkey_runtime", None)
    valkey_available = bool(valkey_runtime is not None and valkey_runtime.is_available)
    status = "ok" if valkey_available else "degraded"
    return JSONResponse(
        status_code=200 if valkey_available else 503,
        content={"status": status, "services": {"valkey": valkey_available}},
    )
