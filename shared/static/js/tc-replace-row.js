/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/*
 * Wires the per-row "Replace from ZIP" icon button in the test-case list.
 *
 * Each replace control is a <form> containing a hidden file input
 * ([data-tc-replace-input]) and a trigger button ([data-tc-replace-trigger]).
 * Clicking the trigger opens the file picker; selecting a file submits the form
 * so the single-case ZIP is uploaded immediately. Listeners are delegated on the
 * document so they keep working after the list partial is re-rendered.
 */
(() => {
  "use strict";

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-tc-replace-trigger]");
    if (!trigger) return;
    const form = trigger.closest("form");
    const input = form ? form.querySelector('input[type="file"][data-tc-replace-input]') : null;
    if (input) input.click();
  });

  document.addEventListener("change", (event) => {
    const input = event.target.closest('input[type="file"][data-tc-replace-input]');
    if (!input || !input.files || input.files.length === 0) return;
    const form = input.closest("form");
    if (form) form.submit();
  });
})();
