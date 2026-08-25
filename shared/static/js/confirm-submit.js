// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// The single home of the `data-confirm` courtesy: a form carrying the attribute
// asks before a destructive or wholesale action. The listener is delegated on
// the document, so a form swapped in later (an htmx refresh, a reordered list
// fragment) keeps it without any per-page wiring. Both modules load this script
// from their `_base.html`, and no page may load a second copy or carry its own
// listener -- two live copies would ask the same question twice.

(() => {
  "use strict";

  document.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-confirm]");
    if (!form) return;
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
})();
