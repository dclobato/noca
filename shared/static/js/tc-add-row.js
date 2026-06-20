// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Shared inline "Add test case" rows for the Web and Arena admin problem-edit
// pages. New cases are appended to `#tc-add-rows` and submitted with the
// enclosing problem form (fields `tc_in_N` / `tc_out_N` / `tc_explanation_N` /
// `tc_is_sample_N`). Inputs are associated with the form by id via the `form`
// attribute (`data-tc-form-id` on the add button, default "edit-form"), so the
// rows may live outside the form element. No-op when the add anchors are absent.

(function () {
  "use strict";

  var tcRowCount = 0;

  function addTcRow(formId) {
    var container = document.getElementById("tc-add-rows");
    if (!container) return;
    var i = tcRowCount++;
    var div = document.createElement("div");
    div.className = "card card-body mb-3 position-relative";
    div.innerHTML =
      '<button type="button" class="btn-close position-absolute top-0 end-0 m-2"' +
      ' aria-label="Remove" data-tc-add-remove></button>' +
      '<div class="row g-2">' +
      '  <div class="col-12 col-md-6">' +
      '    <label class="form-label">Input</label>' +
      '    <textarea name="tc_in_' + i + '" rows="6" form="' + formId + '"' +
      '              maxlength="10240" class="form-control form-control-sm font-monospace"></textarea>' +
      '  </div>' +
      '  <div class="col-12 col-md-6">' +
      '    <label class="form-label">Expected output</label>' +
      '    <textarea name="tc_out_' + i + '" rows="6" form="' + formId + '"' +
      '              maxlength="10240" class="form-control form-control-sm font-monospace"></textarea>' +
      '  </div>' +
      '  <div class="col-12">' +
      '    <label class="form-label">Explanation (optional)</label>' +
      '    <textarea name="tc_explanation_' + i + '" rows="2" form="' + formId + '"' +
      '              class="form-control form-control-sm"' +
      '              placeholder="Explain why this test case produces its expected output…"></textarea>' +
      '    <div class="form-text">Shown to contestants below the sample.</div>' +
      '  </div>' +
      '</div>' +
      '<div class="form-check mt-2">' +
      '  <input type="checkbox" name="tc_is_sample_' + i + '" id="tc_sample_' + i + '"' +
      '         value="true" form="' + formId + '" class="form-check-input">' +
      '  <label class="form-check-label" for="tc_sample_' + i + '">Sample — show input/output to contestants</label>' +
      '</div>';
    div.querySelector("[data-tc-add-remove]").addEventListener("click", function () {
      div.remove();
    });
    container.appendChild(div);
  }

  function init() {
    var btn = document.getElementById("tc-add-row-btn");
    if (!btn) return;
    var formId = btn.dataset.tcFormId || "edit-form";
    btn.addEventListener("click", function () {
      addTcRow(formId);
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
