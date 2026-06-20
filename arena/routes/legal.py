#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena legal document routes."""

from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

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


def _legal_response(request: Request, document_key: str) -> HTMLResponse:
    """Render a legal markdown document."""
    document = _LEGAL_DOCUMENTS[document_key]
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "legal/document.html",
            {
                "title": document["title"],
                "markdown_content": _read_legal_document(document["filename"]),
            },
        )
    )


@router.get("/terms", response_class=HTMLResponse, name="arena_terms_of_service")
async def arena_terms_of_service(request: Request) -> HTMLResponse:
    """Render the Arena Terms of Service."""
    return _legal_response(request, "terms")


@router.get("/privacy", response_class=HTMLResponse, name="arena_privacy_policy")
async def arena_privacy_policy(request: Request) -> HTMLResponse:
    """Render the Arena Privacy Policy."""
    return _legal_response(request, "privacy")
