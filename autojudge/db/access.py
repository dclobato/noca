#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
autojudge/db/access.py

DatabaseAccess — the single worker-facing DB class.

Composed from domain-specific mixin classes. Callers import DatabaseAccess
from autojudge.db (the package __init__) rather than from this module.
"""

from autojudge.db._arena_submission import _ArenaSubmissionMixin
from autojudge.db._judgment import _JudgmentMixin
from autojudge.db._languages import _LanguagesMixin
from autojudge.db._problem import _ProblemMixin
from autojudge.db._profiling import _ProfilingMixin
from autojudge.db._results import _ResultsMixin
from autojudge.db._submission import _SubmissionMixin


class DatabaseAccess(
    _LanguagesMixin,
    _ArenaSubmissionMixin,
    _SubmissionMixin,
    _ProblemMixin,
    _JudgmentMixin,
    _ProfilingMixin,
    _ResultsMixin,
):
    """
    Worker-side database operations.

    Every method is async and expects to be called within a connection context
    (via open_db()). Transactions are committed explicitly after each logical
    unit of work.
    """
