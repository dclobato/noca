#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Template-side contract of the browser form-draft feature.

The shared script is only as good as the markup that binds it: a base layout
that stops loading it, a logout form that forgets its cleanup binding, or an
editor form that loses its key would each silently disable the safety net.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_TEMPLATES = _REPO_ROOT / "web" / "template"
_ARENA_TEMPLATES = _REPO_ROOT / "arena" / "template"
_SHARED_TEMPLATES = _REPO_ROOT / "shared" / "template"

_LOGOUT_FORM_RE = re.compile(r"<form\b[^>]*logout[^>]*>", re.IGNORECASE | re.DOTALL)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_both_base_layouts_load_the_module_and_render_its_hooks() -> None:
    """Every page can settle the owner and the confirmed keys, and logout can clear."""
    for base in (_WEB_TEMPLATES / "_base.html", _ARENA_TEMPLATES / "_base.html"):
        source = _read(base)
        assert "path='noca-form-draft.js'" in source, base
        assert "form_draft_owner(request)" in source, base
        assert "data-noca-draft-owner=" in source, base
        assert "form_draft_confirmed(request)" in source, base
        assert "data-noca-draft-confirmed=" in source, base


def test_every_logout_form_clears_browser_drafts() -> None:
    """A problem statement must not outlive the account that wrote it."""
    found = 0
    for root in (_WEB_TEMPLATES, _ARENA_TEMPLATES, _SHARED_TEMPLATES):
        for path in root.rglob("*.html"):
            for match in _LOGOUT_FORM_RE.finditer(_read(path)):
                found += 1
                assert "data-noca-draft-clear" in match.group(0), f"{path}: {match.group(0)}"
    assert found == 3, "expected the two Arena logout forms and the Web navbar one"


def test_both_problem_definition_forms_bind_a_server_computed_key() -> None:
    """The editor forms opt in with the key the save route later confirms."""
    for template in (
        _ARENA_TEMPLATES / "admin" / "problem_form.html",
        _WEB_TEMPLATES / "admin" / "problems" / "edit.html",
    ):
        source = _read(template)
        assert 'data-noca-draft="{{ view.draft_key }}"' in source, template
        assert 'name="active_tab"' in source and "data-noca-draft-ignore" in source, template


def test_state_carrying_hidden_inputs_are_ignored() -> None:
    """Navigation and server-state fields never ride in a draft."""
    arena = _read(_ARENA_TEMPLATES / "admin" / "problem_form.html")
    form_block = arena[arena.index('<form id="edit-form"') : arena.index("</form>")]
    hidden_inputs = re.findall(r"<input\b[^>]*>", form_block, re.DOTALL)
    assert hidden_inputs, "the Arena Save form carries its return-state hidden inputs"
    for tag in hidden_inputs:
        assert "data-noca-draft-ignore" in tag, tag
    statement_tab = _read(_SHARED_TEMPLATES / "_partials" / "problem_statement_tab.html")
    source_input = re.search(r'<input\b[^>]*name="statement_source"[^>]*>', statement_tab, re.DOTALL)
    assert source_input is not None and "data-noca-draft-ignore" in source_input.group(0)


def test_editor_shell_hosts_the_notice_slot() -> None:
    """Both doors render the notice where the shared shell keeps every notice."""
    shell = _read(_SHARED_TEMPLATES / "_partials" / "problem_editor_shell.html")
    assert 'data-noca-draft-slot="{{ view.header.form_id }}"' in shell


def test_editor_wrappers_refresh_the_visible_editors_after_a_restore() -> None:
    """A restored textarea is pushed back into EasyMDE by both module wrappers."""
    for script in (
        _REPO_ROOT / "arena" / "static" / "js" / "problem-statement-editor.js",
        _REPO_ROOT / "web" / "static" / "js" / "problem-statement-editor.js",
        _REPO_ROOT / "arena" / "static" / "js" / "admin-problem-form.js",
        _REPO_ROOT / "web" / "static" / "js" / "admin-problems-edit.js",
    ):
        assert "noca:form-draft-restored" in _read(script), script
    assert "noca:form-draft-collect" in _read(_REPO_ROOT / "arena" / "static" / "js" / "admin-problem-form.js")
