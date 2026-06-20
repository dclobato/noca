#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
autojudge/db/_languages.py

Mixin for language registry database operations.
"""

from __future__ import annotations

from autojudge.db._base import _DatabaseBase, _utcnow
from shared.db_schema import languages as _language


class _LanguagesMixin(_DatabaseBase):
    """Database operations for the language registry."""

    async def list_languages(self) -> list[dict[str, object]]:
        """
        Return all active languages from the registry.

        Returns:
            List of row dicts for active languages, ordered by id.
        """
        rows = await self._conn.execute(_language.select().where(_language.c.active.is_(True)).order_by(_language.c.id))
        return [dict(row) for row in rows.mappings().all()]

    async def update_language_images(
        self,
        language_id: str,
        *,
        compile_image: str,
        run_image: str,
    ) -> None:
        """
        Update the compile/run image refs for one active language.

        Args:
            language_id: Language primary key.
            compile_image: Canonical compile image reference to persist.
            run_image: Canonical run image reference to persist.

        Raises:
            LookupError: If the language row does not exist.
        """
        result = await self._conn.execute(
            _language.update()
            .where(_language.c.id == language_id)
            .values(
                compile_image=compile_image,
                run_image=run_image,
                updated_at=_utcnow(),
            )
        )
        if result.rowcount == 0:
            raise LookupError(f"Language '{language_id}' not found in database")
        await self._conn.commit()
