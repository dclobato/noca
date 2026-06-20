#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Static contract tests for the Arena problem author controls."""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE = _ROOT / "arena" / "template" / "admin" / "problem_form.html"
_SCRIPT = _ROOT / "arena" / "static" / "js" / "admin-problem-form.js"


def test_authorship_controls_share_a_row_before_the_limit_fields() -> None:
    """The conditional author field stays beside its checkbox above limits."""
    template = _TEMPLATE.read_text(encoding="utf-8")
    authorship_start = template.index('id="author_is_owner"')
    author_field = template.index('id="author-field"')
    limits_start = template.index('id="time_limit_ms"')

    assert "d-flex flex-wrap gap-3 align-items-end" in template[:authorship_start]
    assert authorship_start < author_field < limits_start
    assert 'for="author_is_owner">I\'m problem author</label>' in template
    assert "{% if form.author_is_owner %}hidden{% endif %}" in template


def test_authorship_script_hides_and_disables_the_author_field() -> None:
    """The external script keeps browser validation aligned with visibility."""
    script = _SCRIPT.read_text(encoding="utf-8")

    assert "authorField.hidden = authorIsOwner" in script
    assert "authorInput.disabled = authorIsOwner" in script
    assert "authorInput.required = !authorIsOwner" in script
    assert 'if (authorIsOwner) authorInput.value = ""' in script


def test_license_field_follows_internal_note() -> None:
    """The optional license text field follows the internal management note."""
    template = _TEMPLATE.read_text(encoding="utf-8")
    notes_field = template.index('id="notes"')
    license_field = template.index('id="license"')

    assert notes_field < license_field
    assert 'name="license"' in template[license_field:]
    assert 'maxlength="256"' in template[license_field:]
