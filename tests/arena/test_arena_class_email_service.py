#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for arena_class_email_service."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from jinja2 import UndefinedError

from arena.services.arena_class_email_service import (
    _render,
    send_class_membership_added_email,
    send_class_membership_removed_email,
    send_class_registration_approved_email,
    send_class_registration_denied_email,
    send_class_registration_request_email,
)


def _mock_email_service(*, success: bool = True) -> MagicMock:
    """Return a mock EmailService that reports the given send result."""
    return MagicMock(send_email=MagicMock(return_value=MagicMock(success=success)))


# ---------------------------------------------------------------------------
# Template rendering — StrictUndefined
# ---------------------------------------------------------------------------


def test_registration_request_template_raises_on_missing_context() -> None:
    with pytest.raises(UndefinedError):
        _render("class_registration_request.jinja2", teacher_name="T")


def test_registration_approved_template_raises_on_missing_context() -> None:
    with pytest.raises(UndefinedError):
        _render("class_registration_approved.jinja2", student_name="S")


def test_registration_denied_template_raises_on_missing_context() -> None:
    with pytest.raises(UndefinedError):
        _render("class_registration_denied.jinja2", student_name="S", class_name="C")


def test_membership_added_template_raises_on_missing_context() -> None:
    with pytest.raises(UndefinedError):
        _render("class_membership_added.jinja2", student_name="S")


def test_membership_removed_template_raises_on_missing_context() -> None:
    with pytest.raises(UndefinedError):
        _render("class_membership_removed.jinja2", student_name="S")


# ---------------------------------------------------------------------------
# send_class_registration_request_email
# ---------------------------------------------------------------------------


def test_send_registration_request_sends_to_teacher() -> None:
    svc = _mock_email_service()
    result = send_class_registration_request_email(
        teacher_email="teacher@example.com",
        teacher_name="Prof. Silva",
        student_name="Ana Lima",
        class_name="Algorithms 101",
        members_url="http://arena.test/classes/abc/members",
        email_service=svc,
    )
    assert result is True
    svc.send_email.assert_called_once()
    call = svc.send_email.call_args
    assert call.kwargs["to_email"] == "teacher@example.com"
    assert call.kwargs["to_name"] == "Prof. Silva"
    assert "Algorithms 101" in call.kwargs["subject"]
    assert "Ana Lima" in call.kwargs["text_body"]
    assert "http://arena.test/classes/abc/members" in call.kwargs["text_body"]


def test_send_registration_request_returns_false_on_provider_failure() -> None:
    svc = _mock_email_service(success=False)
    result = send_class_registration_request_email(
        teacher_email="teacher@example.com",
        teacher_name="T",
        student_name="S",
        class_name="C",
        members_url="http://x",
        email_service=svc,
    )
    assert result is False


def test_send_registration_request_returns_false_on_exception() -> None:
    svc = MagicMock(send_email=MagicMock(side_effect=RuntimeError("boom")))
    result = send_class_registration_request_email(
        teacher_email="teacher@example.com",
        teacher_name="T",
        student_name="S",
        class_name="C",
        members_url="http://x",
        email_service=svc,
    )
    assert result is False


# ---------------------------------------------------------------------------
# send_class_registration_approved_email
# ---------------------------------------------------------------------------


def test_send_registration_approved_sends_to_student() -> None:
    svc = _mock_email_service()
    result = send_class_registration_approved_email(
        student_email="ana@example.com",
        student_name="Ana Lima",
        class_name="Algorithms 101",
        class_url="http://arena.test/classes/abc",
        email_service=svc,
    )
    assert result is True
    call = svc.send_email.call_args
    assert call.kwargs["to_email"] == "ana@example.com"
    assert "Algorithms 101" in call.kwargs["subject"]
    assert "approved" in call.kwargs["text_body"].lower()
    assert "http://arena.test/classes/abc" in call.kwargs["text_body"]


def test_send_registration_approved_returns_false_on_exception() -> None:
    svc = MagicMock(send_email=MagicMock(side_effect=RuntimeError("boom")))
    result = send_class_registration_approved_email(
        student_email="s@x.com",
        student_name="S",
        class_name="C",
        class_url="http://x",
        email_service=svc,
    )
    assert result is False


# ---------------------------------------------------------------------------
# send_class_registration_denied_email
# ---------------------------------------------------------------------------


def test_send_registration_denied_with_reason() -> None:
    svc = _mock_email_service()
    result = send_class_registration_denied_email(
        student_email="ana@example.com",
        student_name="Ana Lima",
        class_name="Algorithms 101",
        denial_reason="Class is full.",
        email_service=svc,
    )
    assert result is True
    call = svc.send_email.call_args
    assert call.kwargs["to_email"] == "ana@example.com"
    assert "denied" in call.kwargs["subject"].lower()
    assert "Class is full." in call.kwargs["text_body"]


def test_send_registration_denied_without_reason() -> None:
    svc = _mock_email_service()
    result = send_class_registration_denied_email(
        student_email="ana@example.com",
        student_name="Ana Lima",
        class_name="Algorithms 101",
        denial_reason=None,
        email_service=svc,
    )
    assert result is True
    body = svc.send_email.call_args.kwargs["text_body"]
    assert "Reason:" not in body


def test_send_registration_denied_returns_false_on_exception() -> None:
    svc = MagicMock(send_email=MagicMock(side_effect=RuntimeError("boom")))
    result = send_class_registration_denied_email(
        student_email="s@x.com",
        student_name="S",
        class_name="C",
        denial_reason=None,
        email_service=svc,
    )
    assert result is False


# ---------------------------------------------------------------------------
# send_class_membership_added_email
# ---------------------------------------------------------------------------


def test_send_membership_added_sends_to_student() -> None:
    svc = _mock_email_service()
    result = send_class_membership_added_email(
        student_email="ana@example.com",
        student_name="Ana Lima",
        class_name="Algorithms 101",
        class_url="http://arena.test/classes/abc",
        email_service=svc,
    )
    assert result is True
    call = svc.send_email.call_args
    assert call.kwargs["to_email"] == "ana@example.com"
    assert "Algorithms 101" in call.kwargs["subject"]
    assert "added" in call.kwargs["text_body"].lower()
    assert "http://arena.test/classes/abc" in call.kwargs["text_body"]


def test_send_membership_added_returns_false_on_exception() -> None:
    svc = MagicMock(send_email=MagicMock(side_effect=RuntimeError("boom")))
    result = send_class_membership_added_email(
        student_email="s@x.com",
        student_name="S",
        class_name="C",
        class_url="http://x",
        email_service=svc,
    )
    assert result is False


# ---------------------------------------------------------------------------
# send_class_membership_removed_email
# ---------------------------------------------------------------------------


def test_send_membership_removed_sends_to_student() -> None:
    svc = _mock_email_service()
    result = send_class_membership_removed_email(
        student_email="ana@example.com",
        student_name="Ana Lima",
        class_name="Algorithms 101",
        email_service=svc,
    )
    assert result is True
    call = svc.send_email.call_args
    assert call.kwargs["to_email"] == "ana@example.com"
    assert "Algorithms 101" in call.kwargs["subject"]
    assert "removed" in call.kwargs["text_body"].lower()


def test_send_membership_removed_returns_false_on_exception() -> None:
    svc = MagicMock(send_email=MagicMock(side_effect=RuntimeError("boom")))
    result = send_class_membership_removed_email(
        student_email="s@x.com",
        student_name="S",
        class_name="C",
        email_service=svc,
    )
    assert result is False
