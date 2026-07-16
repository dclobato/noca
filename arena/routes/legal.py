#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena legal document routes."""

from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_users import ArenaUser

router = APIRouter(prefix="/legal", tags=["arena-legal"])

_LEGAL_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "template" / "legal"
_LEGAL_DOCUMENTS = {
    "terms": {
        "filename": "terms_of_service.md",
        "title": "Terms of Service",
    },
    "privacy": {
        "filename": "privacy_policy.md",
        "title": "Privacy Policy",
    },
}


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


def _read_legal_document(filename: str) -> str:
    """Read a legal markdown document from the Arena template tree."""
    path = _LEGAL_TEMPLATE_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Legal document not found.")
    return path.read_text(encoding="utf-8")


def _legal_response(
    request: Request,
    document_key: str,
    current_user: ArenaUser | None,
) -> HTMLResponse:
    """Render a legal markdown document."""
    document = _LEGAL_DOCUMENTS[document_key]
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "legal/document.html",
            {
                "current_user": current_user,
                "title": document["title"],
                "markdown_content": _read_legal_document(document["filename"]),
            },
        )
    )


@router.get("/terms", response_class=HTMLResponse, name="arena_terms_of_service")
async def arena_terms_of_service(
    request: Request,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
) -> HTMLResponse:
    """Render the Arena Terms of Service."""
    return _legal_response(request, "terms", current_user)


@router.get("/privacy", response_class=HTMLResponse, name="arena_privacy_policy")
async def arena_privacy_policy(
    request: Request,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
) -> HTMLResponse:
    """Render the Arena Privacy Policy."""
    return _legal_response(request, "privacy", current_user)
