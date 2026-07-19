// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

(function () {
  function setLanguageSelection(container, checked) {
    container.querySelectorAll('input[name="language_ids"]').forEach(function (input) {
      input.checked = checked;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }

  document.addEventListener("click", function (event) {
    const button = event.target.closest("[data-language-selection-action]");
    if (!button) return;

    const container = button.closest("[data-language-selection]");
    if (!container) return;

    setLanguageSelection(
      container,
      button.dataset.languageSelectionAction === "select-all"
    );
  });
})();
