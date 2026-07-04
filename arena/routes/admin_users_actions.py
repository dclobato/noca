#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin user management — POST action routes.

GET routes (list, profile) live in admin_users.py.
Shared helpers (NavState, guards, redirect builder) live in admin_user_route_support.py.

Every POST route accepts a NavState dependency (capturing the hidden list-navigation
state fields) so redirects restore the admin's previous search/filter context.
"""

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.admin import require_arena_admin
from arena.models.arena_users import ArenaUser
from arena.routes.admin_user_route_support import (
    NavState,
    _build_nav_params,
    _check_last_admin_guard,
    _check_self_guard,
    _choose_redirect,
    _get_target_or_404,
)
from arena.services import admin_user_service, user_ai_credit_service, user_security_notification_service, user_service
from shared.age_check import AgeStatus
from shared.enumerations import ArenaRole
from shared.services.admin_audit import record_admin_action

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["arena-admin"])


@router.post("/users/{user_id}/role", name="arena_admin_user_change_role")
async def admin_user_change_role(
    request: Request,
    user_id: str,
    flash: FlashDep,
    nav: Annotated[NavState, Depends()],
    new_role: str = Form(...),
    confirm_password: str = Form(...),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Change the role of an Arena user (hidden state fields restore navigation context)."""
    if not admin.check_password(confirm_password):
        flash("Incorrect password.", FlashCategory.DANGER)
        return _choose_redirect(request, user_id, nav)
    target = await _get_target_or_404(user_id, session)
    if await _check_self_guard(admin, target, "change the role of", flash):
        return _choose_redirect(request, user_id, nav)
    if await _check_last_admin_guard(target, session, flash):
        return _choose_redirect(request, user_id, nav)
    try:
        resolved_role = ArenaRole(new_role)
    except ValueError:
        flash("Invalid role specified.", FlashCategory.DANGER)
        return _choose_redirect(request, user_id, nav)
    logger.info(
        "Admin %s (%s) change_role on %s (%s)", admin.id, admin.email_normalizado, target.id, target.email_normalizado
    )
    await admin_user_service.change_role(target, resolved_role, session)
    await record_admin_action(
        session,
        request,
        module="arena",
        actor_user_id=admin.id,
        action="role_change",
        target_type="arena_user",
        target_id=target.id,
        detail=f"role={resolved_role.value}",
    )
    await session.commit()
    flash("Role updated.", FlashCategory.SUCCESS)
    return _choose_redirect(request, user_id, nav)


@router.post("/users/{user_id}/toggle-active", name="arena_admin_user_toggle_active")
async def admin_user_toggle_active(
    request: Request,
    user_id: str,
    flash: FlashDep,
    nav: Annotated[NavState, Depends()],
    confirm_password: str = Form(...),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Toggle the active/inactive status of an Arena user (hidden state fields restore navigation context)."""
    if not admin.check_password(confirm_password):
        flash("Incorrect password.", FlashCategory.DANGER)
        return _choose_redirect(request, user_id, nav)
    target = await _get_target_or_404(user_id, session)
    if await _check_self_guard(admin, target, "deactivate", flash):
        return _choose_redirect(request, user_id, nav)
    if target.ativo and await _check_last_admin_guard(target, session, flash):
        return _choose_redirect(request, user_id, nav)
    logger.info(
        "Admin %s (%s) toggle_active on %s (%s)", admin.id, admin.email_normalizado, target.id, target.email_normalizado
    )
    await admin_user_service.toggle_active(target, session)
    await record_admin_action(
        session,
        request,
        module="arena",
        actor_user_id=admin.id,
        action="activate" if target.ativo else "deactivate",
        target_type="arena_user",
        target_id=target.id,
    )
    await session.commit()
    flash("Account activated." if target.ativo else "Account deactivated.", FlashCategory.SUCCESS)
    return _choose_redirect(request, user_id, nav)


@router.post("/users/{user_id}/force-password-change", name="arena_admin_user_force_pw_change")
async def admin_user_force_pw_change(
    request: Request,
    user_id: str,
    flash: FlashDep,
    nav: Annotated[NavState, Depends()],
    confirm_password: str = Form(...),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Toggle the forced password-change flag for an Arena user (hidden state fields restore navigation context)."""
    if not admin.check_password(confirm_password):
        flash("Incorrect password.", FlashCategory.DANGER)
        return _choose_redirect(request, user_id, nav)
    target = await _get_target_or_404(user_id, session)
    logger.info(
        "Admin %s (%s) force_pw_change on %s (%s)",
        admin.id,
        admin.email_normalizado,
        target.id,
        target.email_normalizado,
    )
    was_required = target.precisa_trocar_senha
    await admin_user_service.toggle_force_password_change(target, session)
    await session.commit()
    msg = "Password change required." if target.precisa_trocar_senha else "Password change requirement cleared."
    flash(msg, FlashCategory.SUCCESS)
    if not was_required and target.precisa_trocar_senha:
        try:
            sent = user_security_notification_service.send_admin_password_change_required_email(
                target,
                request.app.state.email_service,
            )
        except Exception:
            logger.exception("Failed to send forced password-change notification to %s", target.email_normalizado)
            sent = False
        if not sent:
            flash("Password change was required, but the notification email could not be sent.", FlashCategory.WARNING)
    return _choose_redirect(request, user_id, nav)


@router.post("/users/{user_id}/toggle-can-edit", name="arena_admin_user_toggle_can_edit")
async def admin_user_toggle_can_edit(
    request: Request,
    user_id: str,
    flash: FlashDep,
    nav: Annotated[NavState, Depends()],
    confirm_password: str = Form(...),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Toggle the problem-base edit privilege for an Arena user (hidden state fields restore navigation context)."""
    if not admin.check_password(confirm_password):
        flash("Incorrect password.", FlashCategory.DANGER)
        return _choose_redirect(request, user_id, nav)
    target = await _get_target_or_404(user_id, session)
    logger.info(
        "Admin %s (%s) toggle_can_edit on %s (%s)",
        admin.id,
        admin.email_normalizado,
        target.id,
        target.email_normalizado,
    )
    await admin_user_service.toggle_can_edit(target, session)
    await session.commit()
    msg = "Problem-edit permission granted." if target.can_edit else "Problem-edit permission revoked."
    flash(msg, FlashCategory.SUCCESS)
    return _choose_redirect(request, user_id, nav)


@router.post("/users/{user_id}/toggle-ranking-visible", name="arena_admin_user_toggle_ranking_visible")
async def admin_user_toggle_ranking_visible(
    request: Request,
    user_id: str,
    flash: FlashDep,
    nav: Annotated[NavState, Depends()],
    confirm_password: str = Form(...),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Toggle the public-ranking visibility flag for an Arena user (hidden state fields restore navigation context)."""
    if not admin.check_password(confirm_password):
        flash("Incorrect password.", FlashCategory.DANGER)
        return _choose_redirect(request, user_id, nav)
    target = await _get_target_or_404(user_id, session)
    logger.info(
        "Admin %s (%s) toggle_ranking_visible on %s (%s)",
        admin.id,
        admin.email_normalizado,
        target.id,
        target.email_normalizado,
    )
    public_profile_cleared = await admin_user_service.toggle_ranking_visible(target, session)
    await session.commit()
    msg = "User is now visible in the ranking." if target.ranking_visible else "User is now hidden from the ranking."
    flash(msg, FlashCategory.SUCCESS)
    if public_profile_cleared:
        flash("Public profile was also disabled.", FlashCategory.WARNING)
    return _choose_redirect(request, user_id, nav)


@router.post("/users/{user_id}/toggle-public-profile", name="arena_admin_user_toggle_public_profile")
async def admin_user_toggle_public_profile(
    request: Request,
    user_id: str,
    flash: FlashDep,
    nav: Annotated[NavState, Depends()],
    confirm_password: str = Form(...),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Toggle the public-profile opt-in flag for an Arena user (hidden state fields restore navigation context)."""
    if not admin.check_password(confirm_password):
        flash("Incorrect password.", FlashCategory.DANGER)
        return _choose_redirect(request, user_id, nav)
    target = await _get_target_or_404(user_id, session)
    logger.info(
        "Admin %s (%s) toggle_public_profile on %s (%s)",
        admin.id,
        admin.email_normalizado,
        target.id,
        target.email_normalizado,
    )
    error = await admin_user_service.toggle_public_profile(target, session)
    if error is not None:
        flash(error, FlashCategory.DANGER)
        return _choose_redirect(request, user_id, nav)
    await session.commit()
    msg = "Public profile enabled." if target.public_profile else "Public profile disabled."
    flash(msg, FlashCategory.SUCCESS)
    return _choose_redirect(request, user_id, nav)


@router.post("/users/{user_id}/remove-photo", name="arena_admin_user_remove_photo")
async def admin_user_remove_photo(
    request: Request,
    user_id: str,
    flash: FlashDep,
    nav: Annotated[NavState, Depends()],
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Remove the profile photo of an Arena user (hidden state fields restore navigation context)."""
    target = await _get_target_or_404(user_id, session)
    logger.info(
        "Admin %s (%s) remove_photo on %s (%s)", admin.id, admin.email_normalizado, target.id, target.email_normalizado
    )
    await admin_user_service.admin_remove_photo(target, session)
    await session.commit()
    flash("Photo removed.", FlashCategory.SUCCESS)
    return _choose_redirect(request, user_id, nav)


@router.post("/users/{user_id}/disable-2fa", name="arena_admin_user_disable_2fa")
async def admin_user_disable_2fa(
    request: Request,
    user_id: str,
    flash: FlashDep,
    nav: Annotated[NavState, Depends()],
    confirm_password: str = Form(...),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Disable two-factor authentication for an Arena user (hidden state fields restore navigation context)."""
    if not admin.check_password(confirm_password):
        flash("Incorrect password.", FlashCategory.DANGER)
        return _choose_redirect(request, user_id, nav)
    target = await _get_target_or_404(user_id, session)
    logger.info(
        "Admin %s (%s) disable_2fa on %s (%s)", admin.id, admin.email_normalizado, target.id, target.email_normalizado
    )
    was_enabled = target.usa_2fa
    await admin_user_service.admin_disable_2fa(target, session)
    await record_admin_action(
        session,
        request,
        module="arena",
        actor_user_id=admin.id,
        action="disable_2fa",
        target_type="arena_user",
        target_id=target.id,
    )
    await session.commit()
    flash("2FA disabled.", FlashCategory.SUCCESS)
    if was_enabled:
        try:
            sent = user_security_notification_service.send_admin_2fa_disabled_email(
                target,
                request.app.state.email_service,
            )
        except Exception:
            logger.exception("Failed to send 2FA-disabled notification to %s", target.email_normalizado)
            sent = False
        if not sent:
            flash("2FA was disabled, but the notification email could not be sent.", FlashCategory.WARNING)
    return _choose_redirect(request, user_id, nav)


@router.post("/users/{user_id}/change-name", name="arena_admin_user_change_name")
async def admin_user_change_name(
    request: Request,
    user_id: str,
    flash: FlashDep,
    nav: Annotated[NavState, Depends()],
    new_name: str = Form(""),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Change the display name of an Arena user (hidden state fields restore navigation context)."""
    target = await _get_target_or_404(user_id, session)
    try:
        logger.info(
            "Admin %s (%s) change_name on %s (%s)",
            admin.id,
            admin.email_normalizado,
            target.id,
            target.email_normalizado,
        )
        await admin_user_service.admin_change_name(target, new_name, session)
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return _choose_redirect(request, user_id, nav)
    await session.commit()
    flash("Name changed.", FlashCategory.SUCCESS)
    return _choose_redirect(request, user_id, nav)


@router.post("/users/{user_id}/date-of-birth", name="arena_admin_user_change_date_of_birth")
async def admin_user_change_date_of_birth(
    request: Request,
    user_id: str,
    flash: FlashDep,
    nav: Annotated[NavState, Depends()],
    date_of_birth: str = Form(""),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Change date of birth and apply age-policy account state."""
    target = await _get_target_or_404(user_id, session)
    try:
        parsed_date = date.fromisoformat(date_of_birth)
    except ValueError:
        flash("Enter a valid date of birth.", FlashCategory.DANGER)
        return _choose_redirect(request, user_id, nav)
    try:
        status = await user_service.update_date_of_birth(target, parsed_date, session)
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return _choose_redirect(request, user_id, nav)
    await session.commit()
    if status == AgeStatus.BLOCKED:
        flash("Date of birth updated. The underage account was deactivated.", FlashCategory.WARNING)
    elif status == AgeStatus.NEEDS_PARENTAL_CONSENT:
        flash("Date of birth updated. Parental consent is now required.", FlashCategory.WARNING)
    else:
        flash("Date of birth updated.", FlashCategory.SUCCESS)
    return _choose_redirect(request, user_id, nav)


@router.post("/users/{user_id}/personal-info", name="arena_admin_user_change_personal_info")
async def admin_user_change_personal_info(
    request: Request,
    user_id: str,
    flash: FlashDep,
    nav: Annotated[NavState, Depends()],
    new_name: str = Form(""),
    date_of_birth: str = Form(""),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Change name and date of birth together in one submission."""
    target = await _get_target_or_404(user_id, session)
    try:
        await admin_user_service.admin_change_name(target, new_name, session)
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return _choose_redirect(request, user_id, nav)
    try:
        parsed_date = date.fromisoformat(date_of_birth)
    except ValueError:
        flash("Enter a valid date of birth.", FlashCategory.DANGER)
        return _choose_redirect(request, user_id, nav)
    try:
        status = await user_service.update_date_of_birth(target, parsed_date, session)
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return _choose_redirect(request, user_id, nav)
    logger.info(
        "Admin %s (%s) change_personal_info on %s (%s)",
        admin.id,
        admin.email_normalizado,
        target.id,
        target.email_normalizado,
    )
    await session.commit()
    if status == AgeStatus.BLOCKED:
        flash("Personal info updated. The underage account was deactivated.", FlashCategory.WARNING)
    elif status == AgeStatus.NEEDS_PARENTAL_CONSENT:
        flash("Personal info updated. Parental consent is now required.", FlashCategory.WARNING)
    else:
        flash("Personal info updated.", FlashCategory.SUCCESS)
    return _choose_redirect(request, user_id, nav)


@router.post("/users/{user_id}/remove-location", name="arena_admin_user_remove_location")
async def admin_user_remove_location(
    request: Request,
    user_id: str,
    flash: FlashDep,
    nav: Annotated[NavState, Depends()],
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Remove the location data for an Arena user (hidden state fields restore navigation context)."""
    target = await _get_target_or_404(user_id, session)
    logger.info(
        "Admin %s (%s) remove_location on %s (%s)",
        admin.id,
        admin.email_normalizado,
        target.id,
        target.email_normalizado,
    )
    await admin_user_service.admin_remove_location(target, session)
    await session.commit()
    flash("Location removed.", FlashCategory.SUCCESS)
    return _choose_redirect(request, user_id, nav)


@router.post("/users/{user_id}/remove-affiliation", name="arena_admin_user_remove_affiliation")
async def admin_user_remove_affiliation(
    request: Request,
    user_id: str,
    flash: FlashDep,
    nav: Annotated[NavState, Depends()],
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Remove the affiliation of an Arena user (hidden state fields restore navigation context)."""
    target = await _get_target_or_404(user_id, session)
    logger.info(
        "Admin %s (%s) remove_affiliation on %s (%s)",
        admin.id,
        admin.email_normalizado,
        target.id,
        target.email_normalizado,
    )
    await admin_user_service.admin_remove_affiliation(target, session)
    await session.commit()
    flash("Affiliation removed.", FlashCategory.SUCCESS)
    return _choose_redirect(request, user_id, nav)


@router.post("/users/{user_id}/reset-api-key", name="arena_admin_user_reset_api_key")
async def admin_user_reset_api_key(
    request: Request,
    user_id: str,
    flash: FlashDep,
    nav: Annotated[NavState, Depends()],
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Clear the personal AI API key of an Arena user (hidden state fields restore navigation context)."""
    target = await _get_target_or_404(user_id, session)
    logger.warning(
        "Admin %s (%s) reset_api_key on %s (%s)",
        admin.id,
        admin.email_normalizado,
        target.id,
        target.email_normalizado,
    )
    await admin_user_service.admin_reset_api_key(target, session)
    await session.commit()
    flash("Personal API key cleared.", FlashCategory.SUCCESS)
    return _choose_redirect(request, user_id, nav)


@router.post("/users/{user_id}/topup-credits", name="arena_admin_user_topup_credits")
async def admin_user_topup_credits(
    request: Request,
    user_id: str,
    flash: FlashDep,
    nav: Annotated[NavState, Depends()],
    quantity: str = Form(...),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Add AI backend credits to an Arena user's balance (hidden state fields restore navigation context)."""
    target = await _get_target_or_404(user_id, session)

    try:
        qty = int(quantity)
    except TypeError, ValueError:
        qty = 0

    if qty <= 0:
        flash("Quantity must be a positive integer.", FlashCategory.DANGER)
    else:
        logger.info(
            "Admin %s (%s) topup_credits %d for %s (%s)",
            admin.id,
            admin.email_normalizado,
            qty,
            target.id,
            target.email_normalizado,
        )
        await user_ai_credit_service.top_up_ai_credits(target, qty, session, admin_id=str(admin.id))
        await session.commit()
        if not user_security_notification_service.send_ai_credits_topped_up_email(
            target, request.app.state.email_service, quantity=qty, balance=target.ai_backend_credits
        ):
            logger.warning("AI credits top-up notification email failed for user %s", target.id)
        flash(
            f"Added {qty} credit{'s' if qty != 1 else ''} to {target.nome}. New balance: {target.ai_backend_credits}.",
            FlashCategory.SUCCESS,
        )

    params: dict[str, str] = {"tab": "credits", **_build_nav_params(nav)}
    profile_url = str(request.url_for("arena_admin_user_profile", user_id=user_id))
    return RedirectResponse(
        url=f"{profile_url}?{'&'.join(f'{k}={v}' for k, v in params.items())}",
        status_code=303,
    )


@router.post("/users/{user_id}/toggle-email-confirmed", name="arena_admin_user_toggle_email_confirmed")
async def admin_user_toggle_email_confirmed(
    request: Request,
    user_id: str,
    flash: FlashDep,
    nav: Annotated[NavState, Depends()],
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Toggle the email-confirmed flag for an Arena user (hidden state fields restore navigation context)."""
    target = await _get_target_or_404(user_id, session)
    logger.warning(
        "Admin %s (%s) toggle_email_confirmed on %s (%s)",
        admin.id,
        admin.email_normalizado,
        target.id,
        target.email_normalizado,
    )
    await admin_user_service.admin_toggle_email_confirmed(target, session)
    await session.commit()
    flash("Email confirmed." if target.email_confirmado else "Email confirmation cleared.", FlashCategory.SUCCESS)
    return _choose_redirect(request, user_id, nav)


@router.post("/users/{user_id}/toggle-parental-consent", name="arena_admin_user_toggle_parental_consent")
async def admin_user_toggle_parental_consent(
    request: Request,
    user_id: str,
    flash: FlashDep,
    nav: Annotated[NavState, Depends()],
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Toggle the parental-consent flag for an Arena user (hidden state fields restore navigation context)."""
    target = await _get_target_or_404(user_id, session)
    logger.warning(
        "Admin %s (%s) toggle_parental_consent on %s (%s)",
        admin.id,
        admin.email_normalizado,
        target.id,
        target.email_normalizado,
    )
    await admin_user_service.admin_toggle_parental_consent(target, session)
    await session.commit()
    flash(
        "Parental consent granted." if target.consentimento_responsavel else "Parental consent revoked.",
        FlashCategory.SUCCESS,
    )
    return _choose_redirect(request, user_id, nav)
