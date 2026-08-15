// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Shared inline "Add sample interaction" rows for the Web and Arena judgment
// pages, on problems with a custom validator. New interactions are appended to
// `#si-add-rows` and submitted with that page's add form
// (fields `si_transcript_N` / `si_explanation_N`). Inputs are associated with the
// form by id via the `form` attribute (`data-si-form-id` on the add button), so
// the rows may live outside the form element.
//
// The add button is disabled once the existing rows plus typed add-rows reach the
// cap. Existing-row removals post immediately, so no client-side pending state is
// involved. The server remains authoritative; this only keeps the button honest.

(function () {
  "use strict";

  var siRowCount = 0;

  function countRemaining() {
    return document.querySelectorAll("#si-list tbody tr[data-id]").length;
  }

  function countPending() {
    var container = document.getElementById("si-add-rows");
    return container ? container.children.length : 0;
  }

  function syncAddButton(btn, maxRows) {
    btn.disabled = countRemaining() + countPending() >= maxRows;
  }

  function bindRemove(button, addButton, maxRows) {
    if (button.dataset.siRemoveBound === "true") return;
    button.dataset.siRemoveBound = "true";
    button.addEventListener("click", function () {
      button.closest(".card").remove();
      syncAddButton(addButton, maxRows);
    });
  }

  function addSiRow(btn, formId, maxRows) {
    var container = document.getElementById("si-add-rows");
    if (!container) return;

    var i = siRowCount++;
    var transcriptId = "si_transcript_" + i;
    var explanationId = "si_explanation_" + i;
    var div = document.createElement("div");
    div.className = "noca-inline-entry mb-3 position-relative";
    div.innerHTML =
      '<button type="button" class="btn-close position-absolute top-0 end-0 m-2"' +
      ' aria-label="Remove" data-si-add-remove></button>' +
      '<div class="row g-2">' +
      '  <div class="col-12">' +
      '    <label class="form-label" for="' + transcriptId + '">Interaction</label>' +
      '    <textarea name="si_transcript_' + i + '" id="' + transcriptId + '" rows="8" form="' + formId + '"' +
      '              class="form-control form-control-sm font-monospace"' +
      '              placeholder="&gt; 3&#10;&lt; 5&#10;&gt; !8"></textarea>' +
      '    <div class="form-text">' +
      '      One message per line, each starting with <code>&gt;&nbsp;</code> (validator)' +
      '      or <code>&lt;&nbsp;</code> (contestant), including the space.' +
      '    </div>' +
      '  </div>' +
      '  <div class="col-12">' +
      '    <label class="form-label" for="' + explanationId + '">Explanation (optional)</label>' +
      '    <textarea name="si_explanation_' + i + '" id="' + explanationId + '" rows="2" form="' + formId + '"' +
      '              class="form-control form-control-sm"' +
      '              placeholder="Explain what happens in this conversation…"></textarea>' +
      '    <div class="form-text">Shown to contestants below the interaction.</div>' +
      '  </div>' +
      '</div>';

    bindRemove(div.querySelector("[data-si-add-remove]"), btn, maxRows);

    container.appendChild(div);
    div.querySelector("#" + transcriptId).focus();
    syncAddButton(btn, maxRows);
  }

  function init() {
    var btn = document.getElementById("si-add-row-btn");
    if (!btn) return;
    var formId = btn.dataset.siFormId || "si-add-form";
    var maxRows = parseInt(btn.dataset.siMax, 10) || 5;
    siRowCount = parseInt(btn.dataset.siNextIndex || "0", 10) || 0;

    syncAddButton(btn, maxRows);
    document.querySelectorAll("[data-si-add-remove]").forEach(function (removeButton) {
      bindRemove(removeButton, btn, maxRows);
    });
    btn.addEventListener("click", function () {
      addSiRow(btn, formId, maxRows);
    });

  }

  document.addEventListener("DOMContentLoaded", init);
})();
