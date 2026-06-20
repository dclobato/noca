#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""JSON profile routes for Arena location and affiliation updates."""

from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_users import ArenaUser
from arena.services import arena_auth_service, user_service
from arena.services.profile_location_service import (
    list_subdivisions,
    reverse_geocode_location,
    search_affiliations,
    update_user_affiliation,
    update_user_location,
)
from arena.services.user_timezone_service import format_user_datetime
from shared.age_check import AgeStatus
from shared.db_schema import languages as languages_table
from shared.db_schema.arena.arena_heatmap import arena_user_submission_heatmap
from shared.db_schema.arena.arena_rating_history import arena_user_rating_history
from shared.services.network_utils import NetworkService

router = APIRouter(tags=["arena-users"])
_SESSION_FLASH_KEY = "_flash_messages"


class LocationUpdateRequest(BaseModel):
    """Payload for updating an Arena user's profile location."""

    country_code: str | None = None
    subdivision_code: str | None = None


class LocationDetectRequest(BaseModel):
    """Payload for browser-coordinate reverse geocoding."""

    latitude: float
    longitude: float


class AffiliationUpdateRequest(BaseModel):
    """Payload for updating an Arena user's affiliation."""

    affiliation_id: str | None = None


class LanguageUpdateRequest(BaseModel):
    """Payload for updating an Arena user's preferred language."""

    language_id: str | None = None


class PersonalDataUpdateRequest(BaseModel):
    """Payload for the unified personal data update."""

    name: str
    date_of_birth: str
    country_code: str | None = None
    subdivision_code: str | None = None
    affiliation_id: str | None = None
    language_id: str | None = None
    prefered_language: Literal["en-US", "pt-BR"] = "en-US"
    ranking_visible: bool | None = None


class ApiKeyUpdateRequest(BaseModel):
    """Payload for setting, replacing, or clearing the user's personal AI API key."""

    api_key: str | None = None


def _json_login_required() -> JSONResponse:
    """Return a consistent JSON response for unauthenticated profile APIs."""
    return JSONResponse({"error": "Authentication required."}, status_code=401)


def _write_warning_flash(request: Request, message: str) -> None:
    """Add a warning message to the current browser session."""
    messages: list[dict[str, str]] = request.session.get(_SESSION_FLASH_KEY, [])
    messages.append({"message": message, "category": "warning"})
    request.session[_SESSION_FLASH_KEY] = messages


@router.get("/user/profile/subdivisions", name="arena_user_profile_subdivisions")
async def arena_user_profile_subdivisions(
    country_code: str = Query(default=""),
    current_user: ArenaUser | None = Depends(get_current_arena_user),
) -> JSONResponse:
    """Return subdivisions for a country selected on the profile page."""
    if current_user is None:
        return _json_login_required()
    try:
        subdivisions = list_subdivisions(country_code)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"subdivisions": [item.__dict__ for item in subdivisions]})


@router.post("/user/profile/location", name="arena_user_profile_location_update")
async def arena_user_profile_location_update(
    payload: LocationUpdateRequest,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Update the current Arena user's country and subdivision."""
    if current_user is None:
        return _json_login_required()
    try:
        update_user_location(
            current_user,
            country_code=payload.country_code,
            subdivision_code=payload.subdivision_code,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    await session.commit()
    return JSONResponse(
        {
            "country_code": current_user.country_code,
            "country_name": current_user.country_name,
            "subdivision_code": current_user.subdivision_code,
            "subdivision_name": current_user.subdivision_name,
        }
    )


@router.post("/user/profile/location/detect", name="arena_user_profile_location_detect")
async def arena_user_profile_location_detect(
    request: Request,
    payload: LocationDetectRequest,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
) -> JSONResponse:
    """Detect profile country/subdivision from coarse browser coordinates."""
    if current_user is None:
        return _json_login_required()
    if not settings.ARENA_REVERSE_GEOCODER_ENABLED:
        return JSONResponse({"error": "Location detection is disabled."}, status_code=503)
    network_service = _get_reverse_geocoder_network_service(request)
    user_agent = str(
        getattr(
            request.app.state,
            "reverse_geocoder_user_agent",
            settings.ARENA_REVERSE_GEOCODER_USER_AGENT or settings.APP_NAME,
        )
    )
    try:
        result = reverse_geocode_location(
            latitude=payload.latitude,
            longitude=payload.longitude,
            endpoint_url=settings.ARENA_REVERSE_GEOCODER_URL,
            user_agent=user_agent,
            network_service=network_service,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(result.__dict__)


@router.get("/user/profile/affiliations/search", name="arena_user_profile_affiliations_search")
async def arena_user_profile_affiliations_search(
    q: str = Query(default=""),
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Search available Arena affiliations by name."""
    if current_user is None:
        return _json_login_required()
    affiliations = await search_affiliations(session, query=q, limit=10)
    return JSONResponse(
        {
            "affiliations": [
                {
                    "id": affiliation.id,
                    "name": affiliation.name,
                    "country_name": affiliation.country_name,
                    "subdivision_name": affiliation.subdivision_name,
                    "url": affiliation.url,
                }
                for affiliation in affiliations
            ]
        }
    )


@router.post("/user/profile/affiliation", name="arena_user_profile_affiliation_update")
async def arena_user_profile_affiliation_update(
    payload: AffiliationUpdateRequest,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Set or clear the current Arena user's selected affiliation."""
    if current_user is None:
        return _json_login_required()
    try:
        affiliation = await update_user_affiliation(
            session,
            current_user,
            affiliation_id=payload.affiliation_id,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    await session.commit()
    return JSONResponse(
        {
            "affiliation_id": affiliation.id if affiliation is not None else None,
            "affiliation_name": affiliation.name if affiliation is not None else None,
        }
    )


@router.post("/user/profile/language", name="arena_user_profile_language_update")
async def arena_user_profile_language_update(
    payload: LanguageUpdateRequest,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Set or clear the current Arena user's preferred language."""
    if current_user is None:
        return _json_login_required()
    language_id = payload.language_id
    language_name: str | None = None
    if language_id is not None:
        lang_result = await session.execute(
            select(languages_table).where(
                languages_table.c.id == language_id,
                languages_table.c.active == True,  # noqa: E712
            ),
        )
        lang_row = lang_result.mappings().first()
        if lang_row is None:
            return JSONResponse({"error": "Language not found or not active."}, status_code=404)
        language_name = lang_row["name"]
    current_user.preferred_language_id = language_id
    await session.commit()
    return JSONResponse({"language_id": language_id, "language_name": language_name})


@router.post("/user/profile/personal-data", name="arena_user_profile_personal_data_update")
async def arena_user_profile_personal_data_update(
    request: Request,
    payload: PersonalDataUpdateRequest,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Atomically update the current user's personal profile data.

    A date-of-birth change applies the Arena age policy. Changes that make the
    user a minor invalidate every active session and log out the current
    browser.

    Args:
        request: Current request, including the authenticated token.
        payload: New values for all personal data fields.
        current_user: Authenticated Arena user, or ``None`` for guests.
        session: Active database session.

    Returns:
        JSONResponse: Updated personal data fields on success.
    """
    if current_user is None:
        return _json_login_required()
    stripped_name = payload.name.strip()
    if not stripped_name or len(stripped_name) > 120:
        return JSONResponse(
            {"error": "name", "message": "Name is required (max 120 characters)."},
            status_code=422,
        )
    try:
        parsed_date_of_birth = date.fromisoformat(payload.date_of_birth)
        if parsed_date_of_birth.isoformat() != payload.date_of_birth:
            raise ValueError
    except ValueError:
        return JSONResponse(
            {"error": "date_of_birth", "message": "Enter a valid date of birth."},
            status_code=422,
        )
    if parsed_date_of_birth > datetime.now(UTC).date():
        return JSONResponse(
            {"error": "date_of_birth", "message": "Date of birth cannot be in the future."},
            status_code=422,
        )
    date_of_birth_changed = current_user.dta_nascimento != parsed_date_of_birth
    try:
        update_user_location(
            current_user,
            country_code=payload.country_code,
            subdivision_code=payload.subdivision_code,
        )
    except ValueError as exc:
        return JSONResponse({"error": "location", "message": str(exc)}, status_code=400)
    try:
        affiliation = await update_user_affiliation(
            session,
            current_user,
            affiliation_id=payload.affiliation_id,
        )
    except ValueError as exc:
        return JSONResponse({"error": "affiliation", "message": str(exc)}, status_code=400)
    language_name: str | None = None
    if payload.language_id is not None:
        lang_row = (
            (
                await session.execute(
                    select(languages_table).where(
                        languages_table.c.id == payload.language_id,
                        languages_table.c.active == True,  # noqa: E712
                    ),
                )
            )
            .mappings()
            .first()
        )
        if lang_row is None:
            return JSONResponse(
                {"error": "language", "message": "Language not found or not active."},
                status_code=400,
            )
        language_name = lang_row["name"]
    current_user.nome = stripped_name
    current_user.preferred_language_id = payload.language_id
    current_user.prefered_language = payload.prefered_language
    if payload.ranking_visible is not None:
        current_user.ranking_visible = payload.ranking_visible
    age_status = await user_service.update_date_of_birth(
        current_user,
        parsed_date_of_birth,
        session,
    )
    await session.commit()
    response_data: dict[str, object] = {
        "name": current_user.nome,
        "date_of_birth": current_user.dta_nascimento.isoformat() if current_user.dta_nascimento else None,
        "country_code": current_user.country_code,
        "country_name": current_user.country_name,
        "subdivision_code": current_user.subdivision_code,
        "subdivision_name": current_user.subdivision_name,
        "affiliation_id": affiliation.id if affiliation is not None else None,
        "affiliation_name": affiliation.name if affiliation is not None else None,
        "language_id": payload.language_id,
        "language_name": language_name,
        "prefered_language": current_user.prefered_language,
        "ranking_visible": current_user.ranking_visible,
    }
    if date_of_birth_changed and age_status != AgeStatus.ALLOWED:
        raw_token: str | None = getattr(request.state, "raw_arena_token", None)
        if raw_token:
            await arena_auth_service.efetuar_logout(raw_token, request.app.state.jwt_service)
        if age_status == AgeStatus.BLOCKED:
            message = "Your account was deactivated because Arena is not available to users under 13."
        else:
            message = "Parental consent is required before you can continue using Arena."
        _write_warning_flash(request, message)
        response_data["redirect_url"] = str(request.url_for("arena_dashboard"))

    response = JSONResponse(response_data)
    if "redirect_url" in response_data:
        response.delete_cookie("arena_access_token", httponly=True, samesite="lax")
    return response


@router.post("/user/profile/api-key", name="arena_user_profile_api_key_update")
async def arena_user_profile_api_key_update(
    payload: ApiKeyUpdateRequest,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Set, replace, or clear the current Arena user's personal AI API key.

    Accepts a plaintext key. An empty or absent value clears any stored key.
    The key is encrypted at rest via ``EncryptedString``; the plaintext value
    is never returned after this call.

    Args:
        payload: New API key value, or ``None`` / empty string to clear.
        current_user: Authenticated Arena user, or ``None`` for guests.
        session: Active database session.

    Returns:
        JSONResponse: ``{"ok": true, "cleared": <bool>}`` on success.
    """
    if current_user is None:
        return _json_login_required()
    stripped = (payload.api_key or "").strip()
    current_user.ai_api_key = stripped if stripped else None
    await session.commit()
    return JSONResponse({"ok": True, "cleared": not bool(stripped)})


@router.get("/user/profile/rating-history", name="arena_user_profile_rating_history")
async def arena_user_profile_rating_history(
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Return the logged-in user's rating history for the last 24 months."""
    if current_user is None:
        return _json_login_required()
    cutoff = datetime.now(tz=UTC) - timedelta(days=730)
    stmt = (
        select(arena_user_rating_history.c.computed_at, arena_user_rating_history.c.rating)
        .where(
            arena_user_rating_history.c.user_id == current_user.id,
            arena_user_rating_history.c.computed_at >= cutoff,
        )
        .order_by(arena_user_rating_history.c.computed_at.asc())
    )
    result = await session.execute(stmt)
    rows = result.fetchall()
    return JSONResponse(
        {
            "history": [
                {
                    "ts": row.computed_at.isoformat(),
                    "ts_display": format_user_datetime(row.computed_at, current_user, "%Y-%m-%d"),
                    "rating": row.rating,
                }
                for row in rows
            ]
        }
    )


@router.get("/user/profile/submission-heatmap", name="arena_user_profile_submission_heatmap")
async def arena_user_profile_submission_heatmap(
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Return the logged-in user's precomputed submission heatmap for the last 52 weeks."""
    if current_user is None:
        return _json_login_required()
    row = (
        (
            await session.execute(
                select(
                    arena_user_submission_heatmap.c.data,
                    arena_user_submission_heatmap.c.range_start,
                    arena_user_submission_heatmap.c.range_end,
                    arena_user_submission_heatmap.c.computed_at,
                ).where(arena_user_submission_heatmap.c.user_id == current_user.id)
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        today = datetime.now(tz=UTC).date()
        start = (today - timedelta(days=363)).isoformat()
        end = today.isoformat()
        return JSONResponse({"heatmap": [], "range_start": start, "range_end": end, "computed_at": None})
    return JSONResponse(
        {
            "heatmap": row["data"],
            "range_start": row["range_start"],
            "range_end": row["range_end"],
            "computed_at": row["computed_at"].isoformat() if row["computed_at"] else None,
        }
    )


def _get_reverse_geocoder_network_service(request: Request) -> NetworkService:
    """Return the app-scoped reverse-geocoder network service or a fallback."""
    network_service = getattr(request.app.state, "reverse_geocoder_network_service", None)
    if isinstance(network_service, NetworkService):
        return network_service
    return NetworkService()
