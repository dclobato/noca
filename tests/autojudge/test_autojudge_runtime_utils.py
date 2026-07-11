from autojudge.runtime_utils import (
    decode_for_text_column,
    is_recoverable_isolate_runtime_error,
)


def test_decode_for_text_column_strips_nul_bytes() -> None:
    # A NUL byte is valid UTF-8 but cannot be stored in a PG text column.
    assert decode_for_text_column(b"before\x00after") == "beforeafter"


def test_decode_for_text_column_replaces_invalid_utf8() -> None:
    result = decode_for_text_column(b"ok\xff")

    assert "\x00" not in result
    assert result.startswith("ok")


def test_decode_for_text_column_preserves_plain_text() -> None:
    assert decode_for_text_column(b"hello world") == "hello world"


def test_is_recoverable_isolate_runtime_error_matches_known_cgroup_pattern() -> None:
    exc = RuntimeError("/sys/fs/cgroup/box-0/cpu.stat: No such file or directory")

    assert is_recoverable_isolate_runtime_error(exc) is True


def test_is_recoverable_isolate_runtime_error_rejects_other_isolate_errors() -> None:
    exc = RuntimeError("/sys/fs/cgroup/box-0/cpu.stat: Permission denied")

    assert is_recoverable_isolate_runtime_error(exc) is False


def test_is_recoverable_isolate_runtime_error_rejects_unrelated_errors() -> None:
    exc = ValueError("database connection lost")

    assert is_recoverable_isolate_runtime_error(exc) is False


def test_is_recoverable_isolate_runtime_error_matches_suspicious_signal_kill() -> None:
    exc = RuntimeError(
        "suspicious sandbox signal kill: process died in 7ms with 692KB, "
        "no output and no stderr (exitsig=9) — treating as judge-environment "
        "failure, not a contestant crash"
    )

    assert is_recoverable_isolate_runtime_error(exc) is True
