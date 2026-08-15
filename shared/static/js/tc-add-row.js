// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Shared inline "Add test case" rows for the Web and Arena judgment-data pages.
// New cases are appended to `#tc-add-rows` and submitted with that page's add
// form (fields `tc_in_N` / `tc_out_N` / `tc_explanation_N` /
// `tc_is_sample_N`). Inputs are associated with the form by id via the `form`
// attribute (`data-tc-form-id` on the add button, default "tc-add-form"), so the
// rows may live outside the form element. No-op when the add anchors are absent.
//
// An interactive problem's cases carry input only -- the input parametrizes the
// validator, which decides the verdict -- and every case is secret, because a
// bare input would reveal a secret without showing what to do with it. So the
// expected-output field and the sample toggle are omitted when the add button
// carries `data-tc-interactive="true"`. The row follows the problem's *stored
// strategy*, never validator-source presence.

(function () {
  "use strict";

  // Starts past the rows the server re-emitted after a failed save, so a new row
  // cannot reuse an index and silently overwrite one the author already typed.
  var tcRowCount = 0;

  function bindRemove(button) {
    if (button.dataset.tcRemoveBound === "true") return;
    button.dataset.tcRemoveBound = "true";
    button.addEventListener("click", function () {
      button.closest(".card").remove();
    });
  }

  function addTcRow(formId, interactive) {
    var container = document.getElementById("tc-add-rows");
    if (!container) return;
    var i = tcRowCount++;
    var inputId = "tc_in_" + i;
    var outputId = "tc_out_" + i;
    var explanationId = "tc_explanation_" + i;
    var div = document.createElement("div");
    div.className = "noca-inline-entry mb-3 position-relative";
    div.innerHTML =
      '<button type="button" class="btn-close position-absolute top-0 end-0 m-2"' +
      ' aria-label="Remove" data-tc-add-remove></button>' +
      '<div class="row g-2">' +
      '  <div class="' + (interactive ? 'col-12' : 'col-12 col-md-6') + '">' +
      '    <label class="form-label" for="' + inputId + '">Input</label>' +
      '    <textarea name="tc_in_' + i + '" id="' + inputId + '" rows="6" form="' + formId + '"' +
      '              maxlength="10240" class="form-control form-control-sm font-monospace"></textarea>' +
      '  </div>' +
      (interactive ? '' :
      '  <div class="col-12 col-md-6">' +
      '    <label class="form-label" for="' + outputId + '">Expected output</label>' +
      '    <textarea name="tc_out_' + i + '" id="' + outputId + '" rows="6" form="' + formId + '"' +
      '              maxlength="10240" class="form-control form-control-sm font-monospace"></textarea>' +
      '  </div>') +
      '  <div class="col-12">' +
      '    <label class="form-label" for="' + explanationId + '">Explanation (optional)</label>' +
      '    <textarea name="tc_explanation_' + i + '" id="' + explanationId + '" rows="2" form="' + formId + '"' +
      '              class="form-control form-control-sm"' +
      '              placeholder="Explain why this test case produces its expected output…"></textarea>' +
      '    <div class="form-text">' +
      (interactive ? 'Notes for judges; interactive cases are never shown to contestants.'
                   : 'Shown to contestants below the sample.') +
      '</div>' +
      '  </div>' +
      '</div>' +
      (interactive ? '' :
      '<div class="form-check mt-2">' +
      '  <input type="checkbox" name="tc_is_sample_' + i + '" id="tc_sample_' + i + '"' +
      '         value="true" form="' + formId + '" class="form-check-input">' +
      '  <label class="form-check-label" for="tc_sample_' + i + '">Sample — show input/output to contestants</label>' +
      '</div>');
    bindRemove(div.querySelector("[data-tc-add-remove]"));
    container.appendChild(div);
    div.querySelector("#" + inputId).focus();
  }

  function init() {
    var btn = document.getElementById("tc-add-row-btn");
    if (!btn) return;
    tcRowCount = parseInt(btn.dataset.tcNextIndex || "0", 10) || 0;
    var formId = btn.dataset.tcFormId || "tc-add-form";
    var interactive = btn.dataset.tcInteractive === "true";
    document.querySelectorAll("[data-tc-add-remove]").forEach(bindRemove);
    btn.addEventListener("click", function () {
      addTcRow(formId, interactive);
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
