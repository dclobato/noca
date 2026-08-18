#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Browser checks for the two problem editors.

A problem is edited through two doors: the *definition* editor (panes, one Save)
and the *judgment-data* editor (pages, immediate actions). Both are checked here,
because each of these pins a defect that shipped past a full green suite and can
only fail in a browser:

* the Markdown editor painted blank inside a hidden pane;
* **Add test case** rendered with no script bound to it;
* a retired pane controller wrote the wrong ``active_tab`` for most panes;
* the unsaved guard binding to a form id that the judgment pages do not have,
  which would let typed rows vanish silently on navigation.

Static assertions on the shipped source stop those recurring. Only a real browser
catches the next one.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from conftest import (
    ARENA_URL,
    WEB_SLUG,
    WEB_URL,
    arena_login,
    first_arena_problem_edit_url,
    requires_credentials,
    requires_web_slug,
    web_login,
)
from playwright.sync_api import Page

pytestmark = requires_credentials


def _open_arena_editor(page: Page, strategy: str) -> None:
    arena_login(page)
    page.goto(f"{ARENA_URL}/admin/problems/new/{strategy}", wait_until="domcontentloaded")
    page.wait_for_selector("#problem-edit-tabs")


def _show_tab(page: Page, tab: str) -> None:
    page.click(f'[data-tab-value="{tab}"]')
    page.wait_for_selector(f"#tab-{tab}.show.active")


# ── The editor paints ────────────────────────────────────────────────────────


def test_an_existing_statement_is_painted_without_being_clicked(page: Page) -> None:
    """CodeMirror has no layout inside `display:none`, so it rendered nothing.

    The statement only appeared once a click forced a reflow. This asserts on
    *rendered* content rather than the wrapper's box: the wrapper takes its height
    from CSS once the pane is visible, so a height check passes even when the
    editor is painting nothing. Measured against this instance with the fix
    reverted, the un-refreshed editor reports 0 rendered lines while its textarea
    holds the full statement -- the text was never lost, only unpainted.

    It opens an existing problem because a statement is needed to notice a
    statement going missing.
    """
    arena_login(page)
    page.goto(first_arena_problem_edit_url(page), wait_until="domcontentloaded")
    page.wait_for_selector("#problem-edit-tabs")
    _show_tab(page, "statement")

    stored = page.input_value("#stmt-md-editor")
    if not stored.strip():
        pytest.skip("the first problem on this instance has an empty statement")

    assert page.locator(".CodeMirror-line").count() > 0, "the editor painted no lines"
    assert len(page.inner_text(".CodeMirror").strip()) > 0, "the editor painted no text"


def test_typing_in_the_statement_survives_a_tab_round_trip(page: Page) -> None:
    """Panes stay mounted and Bootstrap only toggles visibility, so nothing is lost."""
    _open_arena_editor(page, "standard")
    _show_tab(page, "statement")
    page.click(".CodeMirror")
    statement = "A statement typed in the browser."
    page.keyboard.type(statement)

    _show_tab(page, "metadata")
    _show_tab(page, "statement")

    assert "typed in the browser" in page.inner_text(".CodeMirror")
    assert page.input_value("#stmt-md-editor") == statement
    assert page.locator("#stmt-md-editor").evaluate("field => field.validationMessage") == ""


# ── Controls are actually wired ──────────────────────────────────────────────


def test_the_definition_editor_offers_no_judgment_fields(page: Page) -> None:
    """Test cases, the validator and interactions belong to the other door.

    They were panes of this editor until the split. A field for one of them here
    would be a field the Save no longer reads -- input the author would lose.
    """
    arena_login(page)
    page.goto(first_arena_problem_edit_url(page), wait_until="domcontentloaded")
    page.wait_for_selector("#problem-edit-tabs")

    assert page.locator("#tc-add-row-btn").count() == 0
    assert page.locator('input[name="tc_bulk_zip"]').count() == 0
    assert page.locator('input[name="tc_add_zip"]').count() == 0
    assert page.locator('input[name="validator_source_file"]').count() == 0
    assert page.locator('a[href*="/judgment"]').count() >= 1


def test_creating_a_problem_collects_the_definition_only(page: Page) -> None:
    """Creation asks what the problem is; judgment data follows on its own pages."""
    _open_arena_editor(page, "standard")

    assert page.locator("#tc-add-row-btn").count() == 0
    assert page.locator('input[name="tc_bulk_zip"]').count() == 0
    assert page.locator('input[name="validator_source_file"]').count() == 0
    for tab in ("metadata", "statement", "editorial"):
        assert page.locator(f'[data-tab-value="{tab}"]').count() == 1, tab


def test_hidden_metadata_validation_reveals_and_focuses_the_invalid_field(page: Page) -> None:
    """Native validation must not strand Save on a control in a hidden pane."""
    _open_arena_editor(page, "standard")
    page.fill("#title", "")
    _show_tab(page, "statement")

    page.click('.noca-problem-save-bar button[type="submit"]')
    page.wait_for_selector("#tab-metadata.show.active")
    page.wait_for_selector("#title.is-invalid")

    assert page.locator("#title-validation").inner_text() == "This field is required."
    assert page.locator("#title").evaluate("element => document.activeElement === element")


def test_the_category_picker_exposes_combobox_state(page: Page) -> None:
    _open_arena_editor(page, "standard")
    picker = page.locator("#cat-input")

    assert picker.get_attribute("role") == "combobox"
    assert picker.get_attribute("aria-controls") == "cat-dropdown"
    assert picker.get_attribute("aria-expanded") == "false"
    assert page.locator("#cat-dropdown").get_attribute("role") == "listbox"


# ── The tab round-trip ───────────────────────────────────────────────────────


@pytest.mark.parametrize("tab", ["statement", "editorial", "metadata"])
def test_the_open_pane_is_recorded_for_the_save(page: Page, tab: str) -> None:
    """A retired controller wrote "content" for every pane but Limits.

    A failed Save would then re-render on Metadata regardless of where the author
    was working.
    """
    _open_arena_editor(page, "standard")
    _show_tab(page, tab)

    assert page.input_value("#active-tab-input") == tab


def test_a_requested_pane_opens_directly(page: Page) -> None:
    """Routes that return with `?tab=` must select that pane."""
    arena_login(page)
    page.goto(f"{ARENA_URL}/admin/problems/new/standard?tab=statement", wait_until="domcontentloaded")
    page.wait_for_selector("#problem-edit-tabs")

    assert "active" in (page.locator("#tab-statement").get_attribute("class") or "")


def test_a_pane_that_moved_redirects_to_the_page_that_owns_it(page: Page) -> None:
    """`?tab=test-cases` named a pane of this editor; it names a page now.

    An old bookmark or an in-flight link must land on the test cases, not silently
    on Metadata with the author wondering where they went.
    """
    arena_login(page)
    page.goto(f"{first_arena_problem_edit_url(page)}?tab=test-cases", wait_until="domcontentloaded")

    assert page.url.endswith("/judgment/test-cases")


def test_the_strategy_badge_is_not_an_input(page: Page) -> None:
    """The strategy is immutable, so the editor must offer no control for it."""
    _open_arena_editor(page, "interactive")

    assert page.locator('[name="validator_type"]').count() == 0
    assert page.locator(".noca-problem-save-bar").count() == 1


# ── The Contest editor, which additionally has a Limits pane ─────────────────


@requires_web_slug
def test_the_contest_editor_renders_its_four_panes(page: Page) -> None:
    """Contest adds Editorial and keeps a separate Limits pane."""
    web_login(page)
    page.goto(f"{WEB_URL}/c/{WEB_SLUG}/admin/problems/new/interactive", wait_until="domcontentloaded")
    page.wait_for_selector("#problem-edit-tabs")

    for tab in ("metadata", "statement", "editorial", "limits"):
        assert page.locator(f'[data-tab-value="{tab}"]').count() == 1, tab
    for gone in ("test-cases", "sample-interactions"):
        assert page.locator(f'[data-tab-value="{gone}"]').count() == 0, gone


@requires_web_slug
def test_a_contest_statement_is_painted_without_being_clicked(page: Page) -> None:
    """The same hidden-pane defect, on the module where it was first reported."""
    web_login(page)
    page.goto(f"{WEB_URL}/c/{WEB_SLUG}/admin/problems", wait_until="domcontentloaded")
    links = page.eval_on_selector_all('a[href*="/edit"]', 'els => els.map(e => e.getAttribute("href"))')
    if not links:
        pytest.skip("the target contest has no problem to open")

    page.goto(str(links[0]), wait_until="domcontentloaded")
    page.wait_for_selector("#problem-edit-tabs")
    _show_tab(page, "statement")

    stored = page.input_value("#stmt-md-editor")
    if not stored.strip():
        pytest.skip("the first problem in this contest has an empty statement")

    assert page.locator(".CodeMirror-line").count() > 0, "the editor painted no lines"
    assert len(page.inner_text(".CodeMirror").strip()) > 0, "the editor painted no text"


# ── The judgment-data pages ──────────────────────────────────────────────────


@requires_web_slug
def test_the_problem_list_offers_a_judgment_data_action(page: Page) -> None:
    """The second door: definition through the pencil, judgment data beside it."""
    web_login(page)
    page.goto(f"{WEB_URL}/c/{WEB_SLUG}/admin/problems", wait_until="domcontentloaded")

    links = page.locator('a[href*="/judgment"]')
    if links.count() == 0:
        pytest.skip("the target contest has no problem to open")
    assert links.first.get_attribute("title").startswith("Judgment data")


@requires_web_slug
def test_the_judgment_page_renders_and_links_back(page: Page) -> None:
    """The chrome an author navigates by: page nav, and the way back."""
    web_login(page)
    page.goto(f"{WEB_URL}/c/{WEB_SLUG}/admin/problems", wait_until="domcontentloaded")
    links = page.locator('a[href*="/judgment"]')
    if links.count() == 0:
        pytest.skip("the target contest has no problem to open")

    links.first.click()
    page.wait_for_selector("#tc-list")

    assert "Judgment data" in page.inner_text("h1")
    assert page.locator('a:has-text("Problem definition")').count() == 1
    assert page.locator("#tc-add-row-btn").count() == 1
    assert page.locator('input[name="tc_bulk_zip"]').count() == 1


@requires_web_slug
def test_uploading_warns_before_discarding_typed_rows(page: Page, tmp_path: Path) -> None:
    """Typed rows are the only unsaved state, so they are the only thing to warn about."""
    web_login(page)
    page.goto(f"{WEB_URL}/c/{WEB_SLUG}/admin/problems", wait_until="domcontentloaded")
    links = page.locator('a[href*="/judgment"]')
    if links.count() == 0:
        pytest.skip("the target contest has no problem to open")
    links.first.click()
    page.wait_for_selector("#tc-add-row-btn")
    page.click("#tc-add-row-btn")
    assert page.locator("#tc-add-rows > div").count() == 1

    archive = tmp_path / "one-case.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("input.txt", "1\n")
        handle.writestr("output.txt", "1\n")
    messages: list[str] = []
    page.once("dialog", lambda dialog: (messages.append(dialog.message), dialog.dismiss()))

    page.set_input_files('input[name="tc_add_zip"]', str(archive))

    assert messages and "typed but have not saved" in messages[0]
    # Dismissed: the rows stay and the selection is dropped.
    assert page.locator("#tc-add-rows > div").count() == 1
    assert page.eval_on_selector('input[name="tc_add_zip"]', "el => el.files.length") == 0


def _open_arena_judgment(page: Page) -> None:
    """Open the first Arena problem's judgment-data page from the list."""
    arena_login(page)
    page.goto(f"{ARENA_URL}/admin/problems", wait_until="domcontentloaded")
    links = page.locator('a[href*="/judgment"]')
    if links.count() == 0:
        pytest.skip("the target Arena instance has no problem to open")
    links.first.click()
    page.wait_for_selector("#tc-list")


def test_the_arena_list_offers_a_judgment_data_action(page: Page) -> None:
    """The second door: definition through the pencil, judgment data beside it."""
    arena_login(page)
    page.goto(f"{ARENA_URL}/admin/problems", wait_until="domcontentloaded")

    links = page.locator('a[href*="/judgment"]')
    if links.count() == 0:
        pytest.skip("the target Arena instance has no problem to open")
    # Bootstrap moves `title` into `data-bs-original-title` when it initializes a
    # tooltip, so the stable label to assert on is the aria one.
    assert links.first.get_attribute("aria-label").startswith("Judgment data")


def test_the_arena_judgment_page_renders_and_links_back(page: Page) -> None:
    """The chrome an author navigates by: page nav, and the way back."""
    _open_arena_judgment(page)

    assert "Judgment data" in page.inner_text("h1")
    assert page.locator('a:has-text("Problem definition")').count() == 1
    assert page.locator("#tc-add-row-btn").count() == 1
    assert page.locator('input[name="tc_bulk_zip"]').count() == 1


def test_arena_row_actions_are_ordinary_forms_again(page: Page) -> None:
    """No pending model: a row shows what the problem is, not what it would become."""
    _open_arena_judgment(page)
    if page.locator("tr.tc-row").count() == 0:
        pytest.skip("the target problem has no test case")

    assert page.locator('tr.tc-row form[action*="/toggle-sample"]').count() >= 1
    assert page.locator('tr.tc-row form[action*="/delete"]').count() >= 1
    assert page.locator(".tc-undo-btn").count() == 0
    assert page.locator("#tc_remove_ids").count() == 0


def test_adding_a_row_states_the_problem_strategy(page: Page) -> None:
    """Interactive cases carry input only and are always secret.

    The row builder reads the button's flag, so a standard problem must offer the
    expected-output field and the sample toggle an interactive one must not. The
    button itself once rendered with no script bound to it, which is why the click
    is checked rather than the markup.
    """
    _open_arena_judgment(page)
    interactive = page.get_attribute("#tc-add-row-btn", "data-tc-interactive") == "true"
    before = page.locator("#tc-add-rows > div").count()

    page.click("#tc-add-row-btn")

    assert page.locator("#tc-add-rows > div").count() == before + 1
    assert page.locator('#tc-add-rows textarea[name^="tc_in_"]').count() == 1
    expected = 0 if interactive else 1
    assert page.locator('#tc-add-rows textarea[name^="tc_out_"]').count() == expected
    assert page.locator('#tc-add-rows input[name^="tc_is_sample_"]').count() == expected
    input_id = page.locator('#tc-add-rows textarea[name^="tc_in_"]').get_attribute("id")
    assert page.locator(f'#tc-add-rows label[for="{input_id}"]').count() == 1


def test_a_reorder_handle_responds_to_the_keyboard_without_moving_at_the_edge(page: Page) -> None:
    """The first row's Up action is safe but proves the keyboard contract is live."""
    _open_arena_judgment(page)
    handle = page.locator(".noca-drag-handle").first
    if handle.count() == 0 or handle.get_attribute("aria-disabled") == "true":
        pytest.skip("the target problem has no editable row")

    handle.focus()
    page.keyboard.press("ArrowUp")
    page.wait_for_selector("#noca-reorder-status")

    assert "edge of the list" in page.locator("#noca-reorder-status").inner_text()
    assert handle.evaluate("element => document.activeElement === element")


def test_leaving_with_typed_rows_warns(page: Page) -> None:
    """The guard binds to `#edit-form` on the definition editor and to the typed-rows
    form here. If it silently no-ops, typed rows vanish on navigation with no warning
    -- the exact failure the split exists to remove."""
    _open_arena_judgment(page)
    page.click("#tc-add-row-btn")
    page.fill('#tc-add-rows textarea[name^="tc_in_"]', "42\n")
    messages: list[str] = []
    page.once("dialog", lambda dialog: (messages.append(dialog.message), dialog.dismiss()))

    page.locator('a:has-text("Problem definition")').first.click()

    assert messages and "unsaved" in messages[0].lower()
    # Dismissed: still on the page, rows intact.
    assert page.locator("#tc-add-rows > div").count() == 1


def test_a_row_replacement_opens_its_own_file_chooser(page: Page) -> None:
    """Choosing a file replaces that one case immediately, so the trigger must open
    the row's own hidden input rather than a container the page no longer has."""
    _open_arena_judgment(page)
    if page.locator("[data-tc-replace-trigger]").count() == 0:
        pytest.skip("the target problem has no test case to replace")

    with page.expect_file_chooser() as chooser_info:
        page.locator("[data-tc-replace-trigger]").first.click()

    chooser = chooser_info.value
    assert chooser.element.get_attribute("name") == "zip_file"
    assert chooser.page.url.endswith("/judgment/test-cases")


def test_arena_uploading_warns_before_discarding_typed_rows(page: Page, tmp_path: Path) -> None:
    """Typed rows are the only unsaved state, so they are the only thing to warn about."""
    _open_arena_judgment(page)
    page.click("#tc-add-row-btn")
    assert page.locator("#tc-add-rows > div").count() == 1

    archive = tmp_path / "one-case.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("input.txt", "1\n")
        handle.writestr("output.txt", "1\n")
    messages: list[str] = []
    page.once("dialog", lambda dialog: (messages.append(dialog.message), dialog.dismiss()))

    page.set_input_files('input[name="tc_add_zip"]', str(archive))

    assert messages and "typed but have not saved" in messages[0]
    assert page.locator("#tc-add-rows > div").count() == 1
    assert page.eval_on_selector('input[name="tc_add_zip"]', "el => el.files.length") == 0


def test_arena_replacing_all_cases_asks_first(page: Page, tmp_path: Path) -> None:
    """Wholesale replacement states how many cases it would remove."""
    _open_arena_judgment(page)
    archive = tmp_path / "all.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("001.in", "1\n")
        handle.writestr("001.out", "1\n")
    page.set_input_files('input[name="tc_bulk_zip"]', str(archive))
    messages: list[str] = []
    page.once("dialog", lambda dialog: (messages.append(dialog.message), dialog.dismiss()))

    page.click('button:has-text("Replace all")')

    assert messages and "Replace all" in messages[0]
