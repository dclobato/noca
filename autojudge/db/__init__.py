#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
autojudge/db/__init__.py

Public re-exports for the autojudge.db package.

All symbols that callers previously imported from autojudge.db (when it was a
single file) remain importable from this package without any change.
"""

from autojudge.db.access import DatabaseAccess
from autojudge.db.engine import create_worker_engine, open_db
from autojudge.types import (
    ArenaQueuedTestCase,
    ProfilingObservedLimits,
    QueuedArenaSubmission,
    QueuedProfilingRun,
    QueuedSubmission,
    RecoverableArenaSubmissionJob,
    RecoverableProfilingJob,
    RecoverableSubmissionJob,
)

__all__ = [
    "DatabaseAccess",
    "ArenaQueuedTestCase",
    "create_worker_engine",
    "open_db",
    "ProfilingObservedLimits",
    "QueuedArenaSubmission",
    "QueuedProfilingRun",
    "QueuedSubmission",
    "RecoverableArenaSubmissionJob",
    "RecoverableProfilingJob",
    "RecoverableSubmissionJob",
]
