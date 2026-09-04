#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The announcement editor is the statement editor plus links, and nothing else.

Pins the two halves of that contract at the source level, the way
``test_problem_editor_accessibility_contract.py`` pins the core's sync behaviour:
the core only offers the link action behind ``allowLinks``, and the announcement
glue is the one caller that turns it on, for the shared form partial's textarea.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (_ROOT / relative).read_text(encoding="utf-8")


def test_core_offers_the_link_action_only_behind_allow_links() -> None:
    script = _read("shared/static/js/problem-statement-editor-core.js")

    assert "function buildToolbar(allowLinks)" in script
    assert "if (allowLinks) toolbar.push('link');" in script
    assert "toolbar: buildToolbar(options.allowLinks === true)" in script
    assert "'image'" not in script


def test_announcement_glue_mounts_the_core_with_links_on_the_shared_textarea() -> None:
    glue = _read("shared/static/js/announcement-editor.js")
    partial = _read("shared/template/_partials/announcement_form.html")

    assert "textareaId: 'announcement-body-editor'" in glue
    assert "allowLinks: true" in glue
    assert "syncToTextarea()" in glue
    assert 'id="announcement-body-editor"' in partial
    assert 'id="announcement-form"' in partial
