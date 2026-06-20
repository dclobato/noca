#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for shared.services.email_mbox_log."""

from __future__ import annotations

import mailbox
import os
import stat
import threading
from datetime import UTC, datetime
from email.mime.text import MIMEText
from pathlib import Path

import pytest

from shared.services import email_mbox_log

# ---------------------------------------------------------------------------
# current_window_filename
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        (datetime(2026, 6, 3, tzinfo=UTC), "2026-06-01-to-2026-06-15.mbox"),
        (datetime(2026, 6, 15, tzinfo=UTC), "2026-06-01-to-2026-06-15.mbox"),
        (datetime(2026, 6, 20, tzinfo=UTC), "2026-06-16-to-2026-06-30.mbox"),
        (datetime(2026, 2, 20, tzinfo=UTC), "2026-02-16-to-2026-02-28.mbox"),
        (datetime(2024, 2, 20, tzinfo=UTC), "2024-02-16-to-2024-02-29.mbox"),
        (datetime(2026, 1, 31, tzinfo=UTC), "2026-01-16-to-2026-01-31.mbox"),
    ],
)
def test_current_window_filename(when: datetime, expected: str) -> None:
    assert email_mbox_log.current_window_filename(when) == expected


# ---------------------------------------------------------------------------
# append_message
# ---------------------------------------------------------------------------


def _message(body: str = "hello") -> MIMEText:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = "Test"
    msg["Message-ID"] = "<abc@noca.local>"
    return msg


def _append(mbox_dir: Path, body: str = "hello") -> None:
    email_mbox_log.append_message(
        mbox_dir,
        _message(body),
        sender="from@example.com",
        relay="smtp.example.com:587",
        recipients=["to@example.com"],
        when=datetime(2026, 6, 10, 12, tzinfo=UTC),
    )


def test_append_writes_message_with_headers(tmp_path: Path) -> None:
    _append(tmp_path)
    path = tmp_path / "2026-06-01-to-2026-06-15.mbox"
    box = mailbox.mbox(path)
    messages = list(box)
    box.close()
    assert len(messages) == 1
    assert messages[0]["Message-ID"] == "<abc@noca.local>"
    assert messages[0]["X-NOCA-SMTP-Relay"] == "smtp.example.com:587"
    assert messages[0]["X-NOCA-Recipients"] == "to@example.com"
    assert "2026-06-10" in messages[0]["X-NOCA-Delivery-Date"]


def test_append_creates_secure_directory_and_file(tmp_path: Path) -> None:
    mbox_dir = tmp_path / "mailbox"
    email_mbox_log.append_message(
        mbox_dir,
        _message(),
        sender="from@example.com",
        relay="relay:25",
        recipients=["to@example.com"],
        when=datetime(2026, 6, 10, tzinfo=UTC),
    )
    assert stat.S_IMODE(mbox_dir.stat().st_mode) == 0o700
    path = mbox_dir / "2026-06-01-to-2026-06-15.mbox"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_append_does_not_alter_preexisting_dir(tmp_path: Path) -> None:
    # A pre-existing directory must be left untouched: pointing the config at a
    # shared system dir like /var/log must never change its permissions.
    mbox_dir = tmp_path / "loose"
    mbox_dir.mkdir(mode=0o755)
    os.chmod(mbox_dir, 0o755)
    _append(mbox_dir)
    assert stat.S_IMODE(mbox_dir.stat().st_mode) == 0o755
    # The message is still written.
    assert (mbox_dir / "2026-06-01-to-2026-06-15.mbox").exists()


def test_append_is_concurrency_safe(tmp_path: Path) -> None:
    threads = [threading.Thread(target=_append, args=(tmp_path, f"body-{i}")) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    box = mailbox.mbox(tmp_path / "2026-06-01-to-2026-06-15.mbox")
    count = len(list(box))
    box.close()
    assert count == 8


def _mp_worker(mbox_dir_str: str, worker_idx: int, per_worker: int) -> None:
    """Append ``per_worker`` messages; used by the multiprocessing test."""
    from email.mime.text import MIMEText

    mbox_dir = Path(mbox_dir_str)
    for n in range(per_worker):
        msg = MIMEText("body", "plain", "utf-8")
        msg["Message-ID"] = f"<{worker_idx}-{n}@noca.local>"
        email_mbox_log.append_message(
            mbox_dir,
            msg,
            sender="from@example.com",
            relay="smtp.example.com:587",
            recipients=["to@example.com"],
            when=datetime(2026, 6, 10, tzinfo=UTC),
        )


def test_append_is_multiprocess_safe(tmp_path: Path) -> None:
    # Cross-process contention: mailbox's dotlock is non-blocking, so without
    # the bounded retry the second process would raise ExternalClashError and
    # drop records. Assert every message survives.
    import multiprocessing as mp

    workers, per_worker = 4, 25
    # fork inherits the worker function directly, so the test works regardless
    # of whether the tests package is importable by name.
    ctx = mp.get_context("fork")
    procs = [ctx.Process(target=_mp_worker, args=(str(tmp_path), i, per_worker)) for i in range(workers)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0
    box = mailbox.mbox(tmp_path / "2026-06-01-to-2026-06-15.mbox")
    count = len(list(box))
    box.close()
    assert count == workers * per_worker


def test_append_swallows_errors(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    # Point at a path whose parent is a file, so mkdir fails.
    bad_parent = Path("/dev/null/cannot")
    with caplog.at_level(logging.WARNING, logger="shared.services.email_mbox_log"):
        email_mbox_log.append_message(
            bad_parent,
            _message(),
            sender="from@example.com",
            relay="relay:25",
            recipients=["to@example.com"],
            when=datetime(2026, 6, 10, tzinfo=UTC),
        )
    assert any("mbox audit log" in r.message for r in caplog.records)
