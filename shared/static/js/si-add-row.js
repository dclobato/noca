// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Shared inline "Add sample interaction" rows for the Web and Arena admin
// problem-edit pages, on problems with a custom validator. New interactions are
// appended to `#si-add-rows` and submitted with the enclosing problem form
// (fields `si_transcript_N` / `si_explanation_N`). Inputs are associated with the
// form by id via the `form` attribute (`data-si-form-id` on the add button), so
// the rows may live outside the form element.
//
// The add button is disabled once the cap is reached, but the count that matters
// is the one the *save* will produce, not the one currently on screen:
//
//     existing rows not marked for removal  +  pending add-rows
//
// The server applies removals before additions for exactly this reason, so an
// author who marks one of five interactions for removal can add its replacement
// in the same edit. `tc-pending-remove.js` tints a pending row with
// `.tc-pending-removal` and fires `noca:problem-edit-changed`; we recount on that.
// The server remains authoritative — this only keeps the button honest.

(function () {
  "use strict";

  var siRowCount = 0;

  function countRemaining() {
    var rows = document.querySelectorAll("#si-list tbody tr[data-id]");
    var remaining = 0;
    for (var i = 0; i < rows.length; i++) {
      if (!rows[i].classList.contains("tc-pending-removal")) remaining++;
    }
    return remaining;
  }

  function countPending() {
    var container = document.getElementById("si-add-rows");
    return container ? container.children.length : 0;
  }

  function syncAddButton(btn, maxRows) {
    btn.disabled = countRemaining() + countPending() >= maxRows;
  }

  function addSiRow(btn, formId, maxRows) {
    var container = document.getElementById("si-add-rows");
    if (!container) return;

    var i = siRowCount++;
    var div = document.createElement("div");
    div.className = "card card-body mb-3 position-relative";
    div.innerHTML =
      '<button type="button" class="btn-close position-absolute top-0 end-0 m-2"' +
      ' aria-label="Remove" data-si-add-remove></button>' +
      '<div class="row g-2">' +
      '  <div class="col-12">' +
      '    <label class="form-label">Interaction</label>' +
      '    <textarea name="si_transcript_' + i + '" rows="8" form="' + formId + '"' +
      '              class="form-control form-control-sm font-monospace"' +
      '              placeholder="&gt; 3&#10;&lt; 5&#10;&gt; !8"></textarea>' +
      '    <div class="form-text">' +
      '      One message per line, each starting with <code>&gt;&nbsp;</code> (validator)' +
      '      or <code>&lt;&nbsp;</code> (contestant), including the space.' +
      '    </div>' +
      '  </div>' +
      '  <div class="col-12">' +
      '    <label class="form-label">Explanation (optional)</label>' +
      '    <textarea name="si_explanation_' + i + '" rows="2" form="' + formId + '"' +
      '              class="form-control form-control-sm"' +
      '              placeholder="Explain what happens in this conversation…"></textarea>' +
      '    <div class="form-text">Shown to contestants below the interaction.</div>' +
      '  </div>' +
      '</div>';

    div.querySelector("[data-si-add-remove]").addEventListener("click", function () {
      div.remove();
      syncAddButton(btn, maxRows);
    });

    container.appendChild(div);
    syncAddButton(btn, maxRows);
  }

  function init() {
    var btn = document.getElementById("si-add-row-btn");
    if (!btn) return;
    var formId = btn.dataset.siFormId || "edit-form";
    var maxRows = parseInt(btn.dataset.siMax, 10) || 5;

    syncAddButton(btn, maxRows);
    btn.addEventListener("click", function () {
      addSiRow(btn, formId, maxRows);
    });

    // Marking a row for removal (or undoing it) frees or reclaims a slot.
    document.addEventListener("noca:problem-edit-changed", function () {
      syncAddButton(btn, maxRows);
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
