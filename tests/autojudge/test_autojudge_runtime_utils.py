from autojudge.runtime_utils import is_recoverable_isolate_runtime_error


def test_is_recoverable_isolate_runtime_error_matches_known_cgroup_pattern() -> None:
    exc = RuntimeError("/sys/fs/cgroup/box-0/cpu.stat: No such file or directory")

    assert is_recoverable_isolate_runtime_error(exc) is True


def test_is_recoverable_isolate_runtime_error_rejects_other_isolate_errors() -> None:
    exc = RuntimeError("/sys/fs/cgroup/box-0/cpu.stat: Permission denied")

    assert is_recoverable_isolate_runtime_error(exc) is False


def test_is_recoverable_isolate_runtime_error_rejects_unrelated_errors() -> None:
    exc = ValueError("database connection lost")

    assert is_recoverable_isolate_runtime_error(exc) is False
