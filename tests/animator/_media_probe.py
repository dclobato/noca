#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""A statement probe for the team-media tests.

Records every SQL statement the engine executes inside the context, so a test
can assert *which columns* a request selected — the contract under test is that
a ``304`` never touches a blob column and a miss loads exactly one.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine


class StatementProbe:
    """Collects the SQL text of statements executed on the engine."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine.sync_engine
        self.statements: list[str] = []

    def _on_execute(self, conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        self.statements.append(statement)

    def __enter__(self) -> StatementProbe:
        event.listen(self._engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc: object) -> None:
        event.remove(self._engine, "before_cursor_execute", self._on_execute)

    async def __aenter__(self) -> StatementProbe:
        return self.__enter__()

    async def __aexit__(self, *exc: object) -> None:
        self.__exit__(*exc)

    @staticmethod
    def _reads(statement: str, column: str) -> bool:
        """Whether ``statement`` reads ``column`` as a value.

        The metadata query legitimately names the blob columns inside its
        ``length(...) > 0`` presence tests; that is not a read of the payload.
        """
        return re.search(rf"(?<!length\()users_media\.{column}\b", statement) is not None

    def selected_any(self, *columns: str) -> bool:
        """Whether any recorded statement reads the value of one of ``columns``."""
        return any(self._reads(statement, column) for statement in self.statements for column in columns)

    def count_selecting(self, table: str) -> int:
        """Number of recorded statements that read from ``table``."""
        return sum(1 for statement in self.statements if table in statement)

    def order_of(self, column: str) -> int:
        """Index of the first recorded statement naming ``column`` (huge when absent)."""
        return next((i for i, statement in enumerate(self.statements) if self._reads(statement, column)), 1 << 30)
