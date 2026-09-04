#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The "I already have an account" exit from a Google-first signup.

A Google-first signup whose Google address differs from the user's real Arena
address cannot be told apart from a genuine new user, so the callback creates an
inactive account that permanently holds the Google subject -- and the real
account, linking from its profile, hits the subject's UNIQUE constraint. This
module lets the user resolve that themselves at the moment they realise: the
completion form offers a way out that parks the orphan in a session marker and
sends them to the password login; once the *whole* ordinary login chain has run
-- 2FA and forced password change included -- the login's ``next`` lands on a
confirmation page that moves the identity and deletes the orphan in one
transaction.

Nothing here weakens the linking trust model. The identity is bound only from
an authenticated session, on an explicit ``POST`` that names the Google address
being linked, and the marker is time-bounded; possession of a Google address
still cannot take over an account, and a marker planted on a shared browser
cannot link anything silently.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import require_arena_user
from arena.models.arena_users import ArenaUser
from arena.routes.auth_common import _html, _redirect_to
from arena.routes.auth_google import _linked_accounts_redirect
from arena.routes.auth_google_common import (
    PENDING_SIGNUP_UID,
    clear_existing_account_marker,
    peek_existing_account_marker,
    record_google_event,
    require_google_client,
    set_existing_account_marker,
)
from arena.routes.auth_google_complete import _google_email_for, _load_pending_user
from arena.services import google_signup_service, user_security_notification_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/google", tags=["arena-auth"])

_START_OVER_MESSAGE = "Start the Google sign-in again to finish creating your account."
_NOTHING_PENDING_MESSAGE = (
    "There is no Google account waiting to be linked. Start the Google sign-in again if you still want to link one."
)


@router.post("/complete/existing-account", name="arena_google_complete_existing_account")
async def arena_google_complete_existing_account(
    request: Request,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Leave the completion form to sign in to an existing account instead.

    Swaps the "resume the completion form" marker for the "fold this signup into
    an existing account" one and sends the visitor to the password login, whose
    page reads that marker to default its ``next`` to the confirmation page.
    Nothing is written: the orphan stays exactly as the callback left it until
    the confirmation ``POST`` decides its fate, so abandoning here changes nothing.
    """
    require_google_client(request)
    orphan = await _load_pending_user(request, session)
    if orphan is None or google_signup_service.describe_pending_google_signup(orphan) != "needs_completion":
        flash(_START_OVER_MESSAGE, FlashCategory.WARNING)
        return _redirect_to(request, "arena_login")

    google_email = await _google_email_for(session, orphan)
    request.session.pop(PENDING_SIGNUP_UID, None)
    set_existing_account_marker(request, orphan.id)
    flash(
        f"Sign in with your password. Once you are in, you can link {google_email or 'your Google account'} "
        "to that account.",
        FlashCategory.INFO,
    )
    return _redirect_to(request, "arena_login")


async def _load_pending_orphan(request: Request, session: AsyncSession) -> tuple[ArenaUser, str] | None:
    """Resolve the marker to an orphan that can still be folded, or clear it.

    Re-validated on every read rather than trusted from the marker: the orphan
    may have been completed through the Google door, discarded by an
    administrator, or deleted since the marker was set, and each of those means
    there is nothing left to link.
    """
    uid = peek_existing_account_marker(request)
    if uid is None:
        return None
    orphan = await session.get(ArenaUser, uid)
    if orphan is None or google_signup_service.describe_pending_google_signup(orphan) != "needs_completion":
        clear_existing_account_marker(request)
        return None
    google_email = await _google_email_for(session, orphan)
    if google_email is None:
        clear_existing_account_marker(request)
        return None
    return orphan, google_email


@router.get("/link-existing", name="arena_google_link_existing")
async def arena_google_link_existing(
    request: Request,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
    current_user: ArenaUser = Depends(require_arena_user),
) -> Response:
    """Ask the signed-in user to confirm linking the parked Google account.

    Authenticated, and reached only once the whole login chain has issued the
    session -- which is what makes linking here as safe as linking from the
    profile. The page names the Google address, because the one thing it must
    never do is link an account the user did not recognise.
    """
    require_google_client(request)
    pending = await _load_pending_orphan(request, session)
    if pending is None:
        flash(_NOTHING_PENDING_MESSAGE, FlashCategory.WARNING)
        return _linked_accounts_redirect(request)
    _orphan, google_email = pending
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "auth/google_link_existing.html",
            {"google_email": google_email, "account_email": current_user.email_normalizado},
        )
    )


@router.post("/link-existing", name="arena_google_link_existing_submit")
async def arena_google_link_existing_submit(
    request: Request,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
    current_user: ArenaUser = Depends(require_arena_user),
    decision: str = Form(""),
) -> Response:
    """Fold the parked Google-first signup into the signed-in account, or decline.

    ``decision=link`` moves the identity and deletes the orphan in one
    transaction; anything else clears the marker and leaves the orphan as it
    was, so the Google door can still finish it as a separate account later.
    """
    require_google_client(request)
    pending = await _load_pending_orphan(request, session)
    if pending is None:
        flash(_NOTHING_PENDING_MESSAGE, FlashCategory.WARNING)
        return _linked_accounts_redirect(request)
    orphan, google_email = pending

    if decision != "link":
        clear_existing_account_marker(request)
        flash(
            "Nothing was linked. You can still finish that Google sign-up as a separate account "
            "by signing in with Google again.",
            FlashCategory.INFO,
        )
        return _linked_accounts_redirect(request)

    orphan_id = orphan.id
    try:
        async with session.begin_nested():
            await google_signup_service.transfer_pending_signup_identity(
                session, orphan=orphan, user_id=current_user.id
            )
    except IntegrityError:
        # This account already has a Google identity. The orphan is untouched:
        # the savepoint rolled its deletion back with the failed insert.
        logger.info("Google link from a pending signup refused for user %s: identity already in use", current_user.id)
        await record_google_event(
            session,
            request,
            event_type="google_link_conflict",
            severity="warning",
            user_id=current_user.id,
            actor_label=current_user.email_normalizado,
            metadata={"reason": "already_linked", "source": "existing_account"},
        )
        await session.commit()
        clear_existing_account_marker(request)
        flash(
            "That Google account could not be linked: your profile already has a Google account linked. "
            "Unlink it first, then sign in with Google again.",
            FlashCategory.DANGER,
        )
        return _linked_accounts_redirect(request)
    except ValueError:
        # The orphan's state changed between the page and this POST.
        clear_existing_account_marker(request)
        flash(_NOTHING_PENDING_MESSAGE, FlashCategory.WARNING)
        return _linked_accounts_redirect(request)

    await record_google_event(
        session,
        request,
        event_type="google_account_linked",
        user_id=current_user.id,
        actor_label=current_user.email_normalizado,
        metadata={"source": "existing_account"},
    )
    await record_google_event(
        session,
        request,
        event_type="google_signup_discarded",
        severity="warning",
        user_id=current_user.id,
        actor_label=current_user.email_normalizado,
        metadata={"discarded_user_id": orphan_id, "reason": "folded_into_existing_account"},
    )
    await session.commit()
    clear_existing_account_marker(request)

    if not await user_security_notification_service.send_google_linked_email(
        current_user, request.app.state.email_service
    ):
        logger.warning("Google-linked notification email failed for user %s", current_user.id)

    flash(f"Your Google account {google_email} has been linked.", FlashCategory.SUCCESS)
    return _linked_accounts_redirect(request)
