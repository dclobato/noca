/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 The NOCA Authors (see AUTHORS)
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/**
 * Help surface: filter the language roster by name.
 *
 * The roster runs to twenty-plus entries, which is too many to scan for the
 * one language a reader came to check. Filtering is progressive enhancement:
 * the filter control is hidden until this script binds it, so a reader without
 * JavaScript sees the complete roster and nothing that does not work.
 */

document.addEventListener("DOMContentLoaded", () => {
  "use strict";

  const root = document.querySelector("[data-lang-filter]");
  const input = document.querySelector("[data-lang-filter-input]");
  const board = document.querySelector("[data-lang-board]");
  if (!root || !input || !board) {
    return;
  }

  const rows = Array.from(board.querySelectorAll("[data-lang-name]"));
  const empty = board.querySelector("[data-lang-empty]");
  const count = document.querySelector("[data-lang-count]");
  const total = rows.length;
  root.hidden = false;

  /** Show only the rows whose language name contains the query. */
  function apply() {
    const query = input.value.trim().toLowerCase();
    let shown = 0;

    rows.forEach((row) => {
      const name = (row.getAttribute("data-lang-name") || "").toLowerCase();
      const match = query === "" || name.includes(query);
      row.hidden = !match;
      if (match) {
        shown += 1;
      }
    });

    if (empty) {
      empty.hidden = shown !== 0;
    }
    if (count) {
      count.textContent = shown === total ? `${total} languages` : `${shown} of ${total}`;
    }
  }

  input.addEventListener("input", apply);
  input.addEventListener("search", apply);
  apply();
});
