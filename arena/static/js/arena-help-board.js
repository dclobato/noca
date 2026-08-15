/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 The NOCA Authors (see AUTHORS)
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/**
 * Help surface: tie every symbol in the prose to the board cell it is read
 * from, in both directions.
 *
 * A symbol in the text (`[data-help-symbol="a"]`) and the cell it names
 * (`[data-help-cell="a"]`) light together on hover and on keyboard focus. The
 * page is complete without this: the cells already carry their symbol printed
 * beside the label, so the link is a shortcut, never the only way to read it.
 */

document.addEventListener("DOMContentLoaded", () => {
  "use strict";

  const board = document.querySelector("[data-help-board]");
  if (!board) {
    return;
  }

  const cells = new Map();
  board.querySelectorAll("[data-help-cell]").forEach((cell) => {
    const key = cell.getAttribute("data-help-cell");
    if (!cells.has(key)) {
      cells.set(key, []);
    }
    cells.get(key).push(cell);
  });

  const symbols = Array.from(document.querySelectorAll("[data-help-symbol]"));
  if (cells.size === 0 || symbols.length === 0) {
    return;
  }

  /** Light or clear every cell a symbol names. */
  function setLinked(key, on) {
    (cells.get(key) || []).forEach((cell) => cell.classList.toggle("is-linked", on));
  }

  symbols.forEach((symbol) => {
    const key = symbol.getAttribute("data-help-symbol");
    if (!cells.has(key)) {
      return;
    }

    const on = () => setLinked(key, true);
    const off = () => setLinked(key, false);

    symbol.addEventListener("mouseenter", on);
    symbol.addEventListener("mouseleave", off);
    symbol.addEventListener("focus", on);
    symbol.addEventListener("blur", off);
  });

  // The reverse direction: pointing at a cell names it back in the prose.
  cells.forEach((group, key) => {
    group.forEach((cell) => {
      cell.addEventListener("mouseenter", () => setLinked(key, true));
      cell.addEventListener("mouseleave", () => setLinked(key, false));
    });
  });
});
