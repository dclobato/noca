/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/**
 * Problem detail submission confirmation modal.
 *
 * Intercepts the "Submit" button click, populates a Bootstrap 5 modal with
 * the problem title and selected language, and only submits the form when the
 * user confirms.  The form itself is a plain HTML POST form — no AJAX.
 */

// ── Submission confirmation modal ─────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", function () {
  "use strict";

  const form        = document.getElementById("submit-form");
  const submitBtn   = document.getElementById("submit-btn");
  const confirmBtn  = document.getElementById("submit-confirm-btn");
  const modalEl     = document.getElementById("submit-confirm-modal");
  const problemEl   = document.getElementById("submit-modal-problem");
  const languageEl  = document.getElementById("submit-modal-language");
  const langSelect  = document.getElementById("language-select");

  if (!form || !submitBtn || !confirmBtn || !modalEl || !problemEl || !languageEl || !langSelect) {
    return;
  }

  const modal = bootstrap.Modal.getOrCreateInstance(modalEl);

  submitBtn.addEventListener("click", function () {
    // Populate modal with current selections
    problemEl.textContent  = form.dataset.problemTitle || "";
    languageEl.textContent = langSelect.options[langSelect.selectedIndex]
      ? langSelect.options[langSelect.selectedIndex].text
      : "";

    modal.show();
  });

  confirmBtn.addEventListener("click", function () {
    modal.hide();
    form.submit();
  });
});
