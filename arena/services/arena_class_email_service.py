#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Best-effort email notifications for Arena class membership events.

All helpers send email only after the caller has committed the relevant
database change. Delivery failures -- a spent email budget included -- are
caught and logged; they never roll back or raise to the caller.

Every helper takes the ``actor_key``/``tier`` of whoever caused the email:
the requesting student for a registration request, the acting teacher or
admin for the membership decisions.
"""

from __future__ import annotations

import logging

from arena.services.email_rendering import render_email as _render
from shared.services.email_budget import EmailTier
from shared.services.email_service import EmailService

logger = logging.getLogger(__name__)


async def send_class_registration_request_email(
    *,
    teacher_email: str,
    teacher_name: str,
    student_name: str,
    class_name: str,
    members_url: str,
    email_service: EmailService,
    actor_key: str,
    tier: EmailTier = "user",
) -> bool:
    """Notify the class teacher that a student has requested registration.

    Args:
        teacher_email: Teacher's normalised email address.
        teacher_name: Teacher's display name.
        student_name: Requesting student's display name.
        class_name: Name of the class.
        members_url: URL to the class members page.
        email_service: Configured email delivery service.
        actor_key: Budget identity of the actor causing this email.
        tier: Budget tier of that actor.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    try:
        body = _render(
            "class_registration_request.jinja2",
            teacher_name=teacher_name,
            student_name=student_name,
            class_name=class_name,
            members_url=members_url,
        )
        result = await email_service.send_email(
            to_email=teacher_email,
            to_name=teacher_name,
            subject=f'New registration request for "{class_name}"',
            text_body=body,
            actor_key=actor_key,
            tier=tier,
        )
        if not result.success:
            logger.warning("Failed to send registration request email to %s", teacher_email)
        return result.success
    except Exception:
        logger.exception("Error sending registration request email to %s", teacher_email)
        return False


async def send_class_registration_approved_email(
    *,
    student_email: str,
    student_name: str,
    class_name: str,
    class_url: str,
    email_service: EmailService,
    actor_key: str,
    tier: EmailTier = "user",
) -> bool:
    """Notify a student that their registration request was approved.

    Args:
        student_email: Student's normalised email address.
        student_name: Student's display name.
        class_name: Name of the class.
        class_url: URL to the class detail page.
        email_service: Configured email delivery service.
        actor_key: Budget identity of the actor causing this email.
        tier: Budget tier of that actor.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    try:
        body = _render(
            "class_registration_approved.jinja2",
            student_name=student_name,
            class_name=class_name,
            class_url=class_url,
        )
        result = await email_service.send_email(
            to_email=student_email,
            to_name=student_name,
            subject=f'Registration approved: "{class_name}"',
            text_body=body,
            actor_key=actor_key,
            tier=tier,
        )
        if not result.success:
            logger.warning("Failed to send registration approved email to %s", student_email)
        return result.success
    except Exception:
        logger.exception("Error sending registration approved email to %s", student_email)
        return False


async def send_class_registration_denied_email(
    *,
    student_email: str,
    student_name: str,
    class_name: str,
    denial_reason: str | None,
    email_service: EmailService,
    actor_key: str,
    tier: EmailTier = "user",
) -> bool:
    """Notify a student that their registration request was denied.

    Args:
        student_email: Student's normalised email address.
        student_name: Student's display name.
        class_name: Name of the class.
        denial_reason: Optional free-text reason from the teacher/admin.
        email_service: Configured email delivery service.
        actor_key: Budget identity of the actor causing this email.
        tier: Budget tier of that actor.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    try:
        body = _render(
            "class_registration_denied.jinja2",
            student_name=student_name,
            class_name=class_name,
            denial_reason=denial_reason or "",
        )
        result = await email_service.send_email(
            to_email=student_email,
            to_name=student_name,
            subject=f'Registration denied: "{class_name}"',
            text_body=body,
            actor_key=actor_key,
            tier=tier,
        )
        if not result.success:
            logger.warning("Failed to send registration denied email to %s", student_email)
        return result.success
    except Exception:
        logger.exception("Error sending registration denied email to %s", student_email)
        return False


async def send_class_membership_added_email(
    *,
    student_email: str,
    student_name: str,
    class_name: str,
    class_url: str,
    email_service: EmailService,
    actor_key: str,
    tier: EmailTier = "user",
) -> bool:
    """Notify a student that they were directly added to a class.

    Args:
        student_email: Student's normalised email address.
        student_name: Student's display name.
        class_name: Name of the class.
        class_url: URL to the class detail page.
        email_service: Configured email delivery service.
        actor_key: Budget identity of the actor causing this email.
        tier: Budget tier of that actor.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    try:
        body = _render(
            "class_membership_added.jinja2",
            student_name=student_name,
            class_name=class_name,
            class_url=class_url,
        )
        result = await email_service.send_email(
            to_email=student_email,
            to_name=student_name,
            subject=f'You have been added to "{class_name}"',
            text_body=body,
            actor_key=actor_key,
            tier=tier,
        )
        if not result.success:
            logger.warning("Failed to send membership added email to %s", student_email)
        return result.success
    except Exception:
        logger.exception("Error sending membership added email to %s", student_email)
        return False


async def send_class_membership_removed_email(
    *,
    student_email: str,
    student_name: str,
    class_name: str,
    email_service: EmailService,
    actor_key: str,
    tier: EmailTier = "user",
) -> bool:
    """Notify a student that they were removed from a class.

    Args:
        student_email: Student's normalised email address.
        student_name: Student's display name.
        class_name: Name of the class.
        email_service: Configured email delivery service.
        actor_key: Budget identity of the actor causing this email.
        tier: Budget tier of that actor.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    try:
        body = _render(
            "class_membership_removed.jinja2",
            student_name=student_name,
            class_name=class_name,
        )
        result = await email_service.send_email(
            to_email=student_email,
            to_name=student_name,
            subject=f'You have been removed from "{class_name}"',
            text_body=body,
            actor_key=actor_key,
            tier=tier,
        )
        if not result.success:
            logger.warning("Failed to send membership removed email to %s", student_email)
        return result.success
    except Exception:
        logger.exception("Error sending membership removed email to %s", student_email)
        return False
