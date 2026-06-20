"""Tests for NOCA_FORWARDED_ALLOW_IPS validation."""

import pytest

from web.config import Settings


def test_forwarded_allow_ips_accepts_valid_ip_and_cidr() -> None:
    value = Settings.normalize_forwarded_allow_ips(" 127.0.0.1 , 10.0.0.0/8 , ::1 ")
    assert value == "127.0.0.1,10.0.0.0/8,::1"


def test_forwarded_allow_ips_accepts_wildcard_only() -> None:
    value = Settings.normalize_forwarded_allow_ips("*")
    assert value == "*"


def test_forwarded_allow_ips_rejects_invalid_token() -> None:
    with pytest.raises(ValueError, match=r"valid IPs, CIDRs, or '\*' only"):
        Settings.normalize_forwarded_allow_ips("127.0.0.1,not-an-ip")


def test_forwarded_allow_ips_rejects_wildcard_mixed_with_others() -> None:
    with pytest.raises(ValueError, match=r"cannot combine '\*'"):
        Settings.normalize_forwarded_allow_ips("*,127.0.0.1")


# ---------------------------------------------------------------------------
# EMAIL_MBOX_LOG_DIR validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   ", "\t"])
def test_mbox_log_dir_empty_or_blank_is_disabled(value: str | None) -> None:
    assert Settings.normalize_mbox_log_dir(value) is None


def test_mbox_log_dir_accepts_absolute_path() -> None:
    assert Settings.normalize_mbox_log_dir("/var/log/noca/email") == "/var/log/noca/email"


def test_mbox_log_dir_strips_padded_absolute_path() -> None:
    assert Settings.normalize_mbox_log_dir("  /var/log/noca/email  ") == "/var/log/noca/email"


def test_mbox_log_dir_rejects_relative_path() -> None:
    with pytest.raises(ValueError, match="absolute path"):
        Settings.normalize_mbox_log_dir("relative/path")
