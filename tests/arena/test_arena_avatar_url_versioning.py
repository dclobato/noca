#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Every shipped avatar URL carries its cache-busting revision.

The avatar route answers a URL whose ``v`` does not match ``avatar_revision``
with ``max-age=0, must-revalidate``, and the shared image helper emits no
``ETag`` -- so an unversioned avatar is re-downloaded in full on every view.
On a list page that is one full fetch per row, per view, each holding a pool
connection: the Arena ranking issued up to a hundred per page (#199).

The versioned path is the only one a template should take. This test scans
every Arena template so the next list page cannot regress silently.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = PROJECT_ROOT / "arena" / "template"

# Every line that builds the avatar URL. The revision may be appended inside
# the same Jinja expression (``~ '?v=' ~ row.avatar_revision``) or right after
# it (``}}?v={{ user.avatar_revision }}``), so the check is per line, not per
# expression; every shipped call site is a single line.
_AVATAR_URL = re.compile(r"url_for\(\s*['\"]arena_user_avatar_by_id['\"]")


def _avatar_url_expressions() -> list[tuple[Path, int, str]]:
    found: list[tuple[Path, int, str]] = []
    for path in sorted(TEMPLATE_ROOT.rglob("*.html")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _AVATAR_URL.search(line):
                found.append((path.relative_to(PROJECT_ROOT), lineno, line))
    return found


def test_the_scan_sees_the_avatar_route() -> None:
    """Guard against a vacuous pass if the route or the templates are renamed."""
    assert len(_avatar_url_expressions()) >= 8


def test_every_avatar_url_carries_its_revision() -> None:
    """No template may emit the avatar URL without ``?v=<avatar_revision>``."""
    bare = [
        f"{path}:{lineno}: {expression.strip()}"
        for path, lineno, expression in _avatar_url_expressions()
        if "avatar_revision" not in expression
    ]
    assert bare == [], "avatar URLs emitted without a revision:\n  " + "\n  ".join(bare)
