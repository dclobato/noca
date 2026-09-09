/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 The NOCA Authors (see AUTHORS)
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/*
 * Paints every element carrying a per-row badge color.
 *
 * The color is data, not design: it is chosen per category or per collection and
 * stored in the database, so it cannot live in a stylesheet.  Applying it here,
 * from one attribute, keeps it out of inline `style=` attributes in the
 * templates and keeps the two taxonomies from growing separate copies of the
 * same three lines.
 *
 * Usage: <span class="badge" data-swatch-color="#4a9ef2" data-swatch-foreground="#000000">
 *
 * `data-swatch-foreground` is optional and sets the text color; the server
 * computes it for WCAG contrast (BadgeColorMixin.foreground_color).
 */
(() => {
  "use strict";

  const DEFAULT_COLOR = "#6c757d";

  function paintSwatches(root = document) {
    root.querySelectorAll("[data-swatch-color]").forEach((el) => {
      el.style.backgroundColor = el.getAttribute("data-swatch-color") || DEFAULT_COLOR;
      const foreground = el.getAttribute("data-swatch-foreground");
      if (foreground) el.style.color = foreground;
    });
  }

  paintSwatches();

  window.NocaSwatchColor = { paint: paintSwatches };
})();
