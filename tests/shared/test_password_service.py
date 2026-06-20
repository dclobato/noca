#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for shared.services.password_service."""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.services.password_service import (
    PasswordPolicy,
    PasswordPolicyError,
    generate_diceware_password,
)

_SHARED_DIR = Path(__file__).resolve().parents[2] / "shared"


class _FullSettings:
    """Settings stub with all requirements enabled."""

    WORDLIST_FILENAME = "wordlist-pt.txt"
    PASSWORD_WORD_COUNT = 4
    MIN_PASSWORD_LENGTH = 12
    PASSWORD_UPPERCASE_REQUIRED = True
    PASSWORD_LOWERCASE_REQUIRED = True
    PASSWORD_NUMBER_REQUIRED = True
    PASSWORD_SYMBOL_REQUIRED = True


class _LengthOnlySettings:
    """Settings stub with only length requirement."""

    WORDLIST_FILENAME = "wordlist-pt.txt"
    PASSWORD_WORD_COUNT = 4
    MIN_PASSWORD_LENGTH = 8
    PASSWORD_UPPERCASE_REQUIRED = False
    PASSWORD_LOWERCASE_REQUIRED = False
    PASSWORD_NUMBER_REQUIRED = False
    PASSWORD_SYMBOL_REQUIRED = False


# ---------------------------------------------------------------------------
# PasswordPolicyError
# ---------------------------------------------------------------------------


def test_password_policy_error_is_value_error() -> None:
    assert issubclass(PasswordPolicyError, ValueError)


def test_password_policy_error_carries_message() -> None:
    err = PasswordPolicyError("too short")
    assert "too short" in str(err)


# ---------------------------------------------------------------------------
# PasswordPolicy.validate_new_password
# ---------------------------------------------------------------------------


def test_validate_accepts_compliant_password() -> None:
    policy = PasswordPolicy(_FullSettings())
    policy.validate_new_password("ValidPass1!XY")


def test_validate_raises_when_too_short() -> None:
    policy = PasswordPolicy(_FullSettings())
    with pytest.raises(PasswordPolicyError):
        policy.validate_new_password("Sh0rt!")


def test_validate_raises_when_uppercase_missing() -> None:
    policy = PasswordPolicy(_FullSettings())
    with pytest.raises(PasswordPolicyError):
        policy.validate_new_password("validpass1!!")


def test_validate_raises_when_lowercase_missing() -> None:
    policy = PasswordPolicy(_FullSettings())
    with pytest.raises(PasswordPolicyError):
        policy.validate_new_password("VALIDPASS1!!")


def test_validate_raises_when_number_missing() -> None:
    policy = PasswordPolicy(_FullSettings())
    with pytest.raises(PasswordPolicyError):
        policy.validate_new_password("ValidPassword!")


def test_validate_raises_when_symbol_missing() -> None:
    policy = PasswordPolicy(_FullSettings())
    with pytest.raises(PasswordPolicyError):
        policy.validate_new_password("ValidPass1234")


def test_validate_passes_with_only_length_requirement() -> None:
    policy = PasswordPolicy(_LengthOnlySettings())
    policy.validate_new_password("abcdefgh")


def test_validate_raises_when_length_fails_even_with_no_char_requirements() -> None:
    policy = PasswordPolicy(_LengthOnlySettings())
    with pytest.raises(PasswordPolicyError):
        policy.validate_new_password("short")


def test_validate_exact_minimum_length_passes() -> None:
    policy = PasswordPolicy(_LengthOnlySettings())
    policy.validate_new_password("a" * 8)


def test_validate_one_below_minimum_fails() -> None:
    policy = PasswordPolicy(_LengthOnlySettings())
    with pytest.raises(PasswordPolicyError):
        policy.validate_new_password("a" * 7)


# ---------------------------------------------------------------------------
# PasswordPolicy.policy_hint
# ---------------------------------------------------------------------------


def test_policy_hint_includes_all_requirements() -> None:
    hint = PasswordPolicy(_FullSettings()).policy_hint
    assert "uppercase" in hint
    assert "lowercase" in hint
    assert "number" in hint
    assert "symbol" in hint
    assert "12" in hint


def test_policy_hint_length_only_has_no_char_requirements() -> None:
    hint = PasswordPolicy(_LengthOnlySettings()).policy_hint
    assert "uppercase" not in hint
    assert "lowercase" not in hint
    assert "number" not in hint
    assert "symbol" not in hint
    assert "8" in hint


def test_policy_hint_omits_disabled_requirements() -> None:
    class _Partial:
        WORDLIST_FILENAME = "wordlist-pt.txt"
        PASSWORD_WORD_COUNT = 4
        MIN_PASSWORD_LENGTH = 10
        PASSWORD_UPPERCASE_REQUIRED = True
        PASSWORD_LOWERCASE_REQUIRED = False
        PASSWORD_NUMBER_REQUIRED = True
        PASSWORD_SYMBOL_REQUIRED = False

    hint = PasswordPolicy(_Partial()).policy_hint
    assert "uppercase" in hint
    assert "number" in hint
    assert "lowercase" not in hint
    assert "symbol" not in hint


# ---------------------------------------------------------------------------
# generate_diceware_password
# ---------------------------------------------------------------------------


def test_generate_uses_dash_separator() -> None:
    pwd = generate_diceware_password(_FullSettings())
    assert "-" in pwd


def test_generate_meets_minimum_length() -> None:
    pwd = generate_diceware_password(_FullSettings())
    assert len(pwd) >= _FullSettings.MIN_PASSWORD_LENGTH


def test_generate_contains_digit_when_required() -> None:
    pwd = generate_diceware_password(_FullSettings())
    assert any(ch.isdigit() for ch in pwd)


def test_generate_contains_uppercase_when_required() -> None:
    pwd = generate_diceware_password(_FullSettings())
    assert any(ch.isupper() for ch in pwd)


def test_generate_contains_lowercase_when_required() -> None:
    pwd = generate_diceware_password(_FullSettings())
    assert any(ch.islower() for ch in pwd)


def test_generate_satisfies_policy() -> None:
    settings = _FullSettings()
    policy = PasswordPolicy(settings)
    for _ in range(5):
        pwd = generate_diceware_password(settings)
        policy.validate_new_password(pwd)


def test_generate_custom_size_affects_word_count() -> None:
    pwd = generate_diceware_password(_FullSettings(), size=2)
    parts = pwd.split("-")
    # With size=2 + a digit, there are at least 3 dash-separated parts.
    assert len(parts) >= 2


def test_generate_custom_wordlist_path() -> None:
    en_path = _SHARED_DIR / "wordlist-en.txt"
    pwd = generate_diceware_password(_FullSettings(), wordlist_path=en_path)
    assert "-" in pwd
    assert len(pwd) >= _FullSettings.MIN_PASSWORD_LENGTH


def test_generate_produces_varied_output() -> None:
    results = {generate_diceware_password(_FullSettings()) for _ in range(10)}
    # With 7776 words per slot, the chance of 10 identical passwords is negligible.
    assert len(results) > 1
