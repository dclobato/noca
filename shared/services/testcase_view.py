#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Cross-module presentation model for the shared test-case list partial.

Both the Web (contest admin) and Arena modules render
``shared/template/_partials/testcase_list_table.html``. That partial must stay
free of module-specific ``url_for`` route names and ORM attribute access, so each
module adapts its own data into a list of :class:`TestCaseRowView` objects whose
URLs are pre-built by the calling route. The ``is_large`` flag (whether the case
exceeds the inline-edit gate ``shared.tc_zip.MAX_INLINE_TESTCASE_BYTES``) is
baked in here so the template needs no Jinja global.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TestCaseRowView:
    """One test-case row for the shared list table.

    Attributes:
        id: Test-case identifier (used for DOM ids and pending-removal tracking).
        ordinal: 1-based display position.
        is_sample: Whether the case is shown to contestants as a sample.
        has_explanation: Whether an author explanation is present.
        input_preview: Short, already-truncated input preview text.
        output_preview: Short, already-truncated output preview text.
        input_size_bytes: Normalized (LF) on-disk input size.
        output_size_bytes: Normalized (LF) on-disk output size.
        is_large: True when either side exceeds the inline-edit gate.
        edit_url: Per-row edit/view URL.
        download_url: Per-row single-case ZIP download URL.
        replace_url: Per-row offline ZIP replace POST URL.
        move_url: Per-row reorder POST URL.
        toggle_sample_url: Per-row sample/secret toggle POST URL.
    """

    id: str
    ordinal: int
    is_sample: bool
    has_explanation: bool
    input_preview: str
    output_preview: str
    input_size_bytes: int
    output_size_bytes: int
    is_large: bool
    edit_url: str
    download_url: str
    replace_url: str
    move_url: str
    toggle_sample_url: str
