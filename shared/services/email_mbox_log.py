#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Append-only mbox audit log of successfully delivered emails.

Writes a full RFC 822 copy of every accepted SMTP delivery into a locked,
date-rotated mbox file. Files rotate on fixed 15-day calendar windows (days
1-15 and 16-end-of-month) computed in UTC, so filenames are stable regardless
of server local time.

Because the logged messages can contain passwords, OTPs, activation links, and
reset tokens, the log directory is created ``0o700`` and each mbox file
``0o600`` regardless of the process umask.
"""

from __future__ import annotations

import calendar
import logging
import mailbox
import os
import stat
import threading
import time
from datetime import datetime
from email.message import Message
from pathlib import Path

logger = logging.getLogger(__name__)

_DIR_MODE = 0o700
_FILE_MODE = 0o600

# mailbox.mbox dotlocking keys its temp file on the PID, so it serializes only
# across processes. This in-process lock serializes concurrent threads (e.g.
# FastAPI sync handlers running in the threadpool) within a single process.
_process_lock = threading.Lock()

# mailbox's inter-process lock (flock/lockf + dotlock) is non-blocking and
# raises ExternalClashError on contention. Retry with bounded backoff so a
# concurrent writer in another process never causes a dropped audit record.
_LOCK_RETRY_ATTEMPTS = 100
_LOCK_RETRY_DELAY = 0.1  # seconds; up to ~10s of total contention wait


def current_window_filename(when: datetime) -> str:
    """Return the mbox filename for the 15-day window containing ``when``.

    Each month is split into two windows: days 1-15 and days 16-end of month.

    Args:
        when: Reference instant (interpreted in its own tz; callers pass UTC).

    Returns:
        Filename such as ``2026-06-01-to-2026-06-15.mbox`` or
        ``2026-06-16-to-2026-06-30.mbox``.
    """
    year = when.year
    month = when.month
    if when.day <= 15:
        start_day, end_day = 1, 15
    else:
        start_day = 16
        end_day = calendar.monthrange(year, month)[1]
    prefix = f"{year:04d}-{month:02d}"
    return f"{prefix}-{start_day:02d}-to-{prefix}-{end_day:02d}.mbox"


def _ensure_secure_dir(mbox_dir: Path) -> None:
    """Create ``mbox_dir`` with ``0o700`` perms, never altering an existing dir.

    Permissions are only set on a directory this function actually creates, so a
    misconfigured value pointing at a pre-existing shared directory (e.g.
    ``/var/log``) is left untouched.
    """
    if mbox_dir.exists():
        return
    # mkdir honors the umask, so chmod the freshly created leaf explicitly.
    mbox_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(mbox_dir, _DIR_MODE)


def _ensure_secure_file(path: Path) -> None:
    """Create the mbox file if missing and keep it ``0o600``.

    The file is owned by this service, so its permissions are always enforced.
    """
    if not path.exists():
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, _FILE_MODE)
        os.close(fd)
    if stat.S_IMODE(path.stat().st_mode) != _FILE_MODE:
        os.chmod(path, _FILE_MODE)


def _lock_with_retry(box: mailbox.mbox) -> None:
    """Acquire the mbox inter-process lock, retrying on contention.

    Raises:
        Exception: The last lock error if every attempt clashed.
    """
    last_exc: Exception | None = None
    for _ in range(_LOCK_RETRY_ATTEMPTS):
        try:
            box.lock()
            return
        except (mailbox.ExternalClashError, OSError) as exc:
            last_exc = exc
            time.sleep(_LOCK_RETRY_DELAY)
    assert last_exc is not None
    raise last_exc


def append_message(
    mbox_dir: Path,
    mime_message: Message,
    *,
    sender: str,
    relay: str,
    recipients: list[str],
    when: datetime,
) -> None:
    """Append a delivered message to the current locked, rotated mbox file.

    Failures are logged and swallowed: the email was already delivered when this
    is called, so audit logging must never turn a success into a failure.

    Args:
        mbox_dir: Directory holding the rotated mbox files.
        mime_message: The exact RFC 822 message handed to the SMTP server.
        sender: Bare envelope sender address (no display name).
        relay: SMTP relay endpoint the message was handed off to (``host:port``).
        recipients: Recipients accepted by the relay.
        when: UTC instant of delivery; drives rotation and the delivery date.
    """
    try:
        _ensure_secure_dir(mbox_dir)
        path = mbox_dir / current_window_filename(when)
        _ensure_secure_file(path)

        msg = mailbox.mboxMessage(mime_message)
        # set_from() expects a time.struct_time, not a datetime.
        msg.set_from(sender, when.utctimetuple())
        msg["X-NOCA-SMTP-Relay"] = relay
        msg["X-NOCA-Delivery-Date"] = when.isoformat()
        msg["X-NOCA-Recipients"] = ", ".join(recipients)

        with _process_lock:
            box = mailbox.mbox(path)
            _lock_with_retry(box)
            try:
                box.add(msg)
                box.flush()
            finally:
                box.unlock()
                box.close()
    except Exception:  # noqa: BLE001 - audit logging must never break delivery
        logger.warning("Failed to write email to mbox audit log", exc_info=True)
