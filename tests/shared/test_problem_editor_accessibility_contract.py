#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Static accessibility contracts shared by the two problem editors."""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    """Read a repository file used by a static UI contract."""
    return (_ROOT / path).read_text(encoding="utf-8")


def test_hidden_pane_validation_captures_native_errors_and_reveals_the_field() -> None:
    script = _read("shared/static/js/problem-edit-validate.js")

    assert 'document.addEventListener("invalid"' in script
    assert "window.bootstrap.Tab.getOrCreateInstance(trigger).show()" in script
    assert 'feedback.className = "invalid-feedback"' in script
    assert 'field.scrollIntoView({ block: "center" })' in script


def test_statement_editor_syncs_before_native_required_validation() -> None:
    """Keep the required textarea value and custom validity current."""
    script = _read("shared/static/js/problem-statement-editor-core.js")

    assert "forceSync: true" in script
    assert "textarea.dispatchEvent(new Event('input', { bubbles: true }))" in script


def test_dynamic_rows_bind_labels_to_the_generated_controls() -> None:
    testcase_script = _read("shared/static/js/tc-add-row.js")
    interaction_script = _read("shared/static/js/si-add-row.js")

    assert "for=\"' + inputId" in testcase_script
    assert "for=\"' + outputId" in testcase_script
    assert "for=\"' + explanationId" in testcase_script
    assert "for=\"' + transcriptId" in interaction_script
    assert "for=\"' + explanationId" in interaction_script


def test_reorder_handles_support_arrow_keys_and_announce_results() -> None:
    script = _read("shared/static/js/tc-reorder-sortable.js")
    testcase_table = _read("shared/template/_partials/testcase_list_table.html")
    interaction_table = _read("shared/template/_partials/sample_interaction_list_table.html")

    assert 'event.key === "Enter" || event.key === " "' in script
    assert 'event.key !== "ArrowUp" && event.key !== "ArrowDown"' in script
    assert 'region.setAttribute("aria-live", "polite")' in script
    assert 'aria-keyshortcuts="ArrowUp ArrowDown"' in testcase_table
    assert 'aria-keyshortcuts="ArrowUp ArrowDown"' in interaction_table


def test_validator_status_and_category_search_expose_live_state() -> None:
    validator = _read("shared/template/_partials/validator_status_badge.html")
    metadata = _read("arena/template/_partials/problem_tab_metadata.html")
    category_script = _read("arena/static/js/admin-problem-form.js")
    # The ARIA state machine is the shared `.arena-combo` controller, used by
    # the category picker and the Source/Author/License comboboxes alike.
    combo_controller = _read("arena/static/js/arena-combo-listbox.js")

    assert 'role="status"' in validator
    assert 'aria-live="polite"' in validator
    assert 'role="combobox"' in metadata
    assert 'role="listbox"' in metadata
    assert 'aria-activedescendant=""' in metadata
    assert 'input.setAttribute("aria-expanded", "true")' in combo_controller
    assert 'input.setAttribute("aria-expanded", "false")' in combo_controller
    assert 'input.setAttribute("aria-activedescendant", activeOption.id)' in combo_controller
    assert "Could not load categories. Try again." in category_script


def test_language_file_picker_and_testcase_empty_state_are_actionable() -> None:
    """Keep shared upload hints and the first-case path explicit."""
    validator = _read("shared/template/_partials/judgment_validator_page.html")
    testcase_table = _read("shared/template/_partials/testcase_list_table.html")
    script = _read("shared/static/js/language-file-accept.js")
    arena_page = _read("arena/template/admin/problem_judgment_validator.html")
    web_page = _read("web/template/admin/problems/judgment_validator.html")

    assert "data-language-file-accept" in validator
    assert 'select.addEventListener("change", applyAccept)' in script
    assert 'fileInput.setAttribute("accept", extension)' in script
    assert "language-file-accept.js" in arena_page
    assert "language-file-accept.js" in web_page
    assert "Choose <strong>Add test case</strong> below" in testcase_table


def test_row_feedback_honors_theme_motion_and_touch_input() -> None:
    """Keep row highlighting perceivable without forcing color or motion."""
    script = _read("shared/static/js/highlight-row.js")
    css = _read("shared/static/css/common.css")

    assert "#ffeb3b" not in script
    assert "prefers-reduced-motion: reduce" in script
    assert "reduceMotion ? 'auto' : 'smooth'" in script
    assert "background-color: var(--bs-warning-bg-subtle)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "@media (pointer: coarse)" in css
    assert "min-height: 2.75rem" in css
