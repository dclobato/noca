// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Shared pending-removal + undo model for the Web and Arena admin problem-edit
// pages. Test cases are marked for removal client-side and only deleted when the
// problem form is saved (the ids are submitted in the hidden `tc_remove_ids`
// input). The enclosing form is found via `form[data-tc-pending-form]`, falling
// back to `#edit-form`.
//
// A problem with a custom interactive validator may legitimately end up with no
// test cases (interactive judging never reads test-case files), so the "at least
// one must remain" guard below stands down for those problems. That holds in two
// cases, and the backend applies the same rule:
//
//   * a validator is already configured — the templates set
//     `data-allow-empty="true"` on the `tc_remove_ids` input; or
//   * a validator source file is staged in this very submit, which the Arena form
//     allows because its single Save carries the validator too. Only an upload
//     bound to the pending form counts, so the Web edit page — where the validator
//     upload is a separate, immediate form — is unaffected.

(function () {
  var pendingRemovals = new Set();
  var form = document.querySelector('form[data-tc-pending-form]') || document.getElementById('edit-form');

  function syncHiddenInput() {
    var el = document.getElementById('tc_remove_ids');
    if (el) el.value = Array.from(pendingRemovals).join(',');
    document.dispatchEvent(new CustomEvent('noca:problem-edit-changed'));
  }

  function validatorStagedInThisSave() {
    var input = form && form.elements ? form.elements['validator_source_file'] : null;
    return !!(input && input.files && input.files.length > 0);
  }

  function allowEmpty() {
    var el = document.getElementById('tc_remove_ids');
    if (el && el.dataset.allowEmpty === 'true') return true;
    return validatorStagedInThisSave();
  }

  function totalRows() {
    return document.querySelectorAll('.tc-row').length;
  }

  function removingEverything() {
    return !allowEmpty() && pendingRemovals.size > 0 && pendingRemovals.size >= totalRows();
  }

  function showWarning(msg) {
    var w = document.getElementById('tc-remove-warning');
    if (!w) return;
    w.textContent = msg;
    w.classList.remove('d-none');
  }

  function clearWarning() {
    var w = document.getElementById('tc-remove-warning');
    if (w) { w.textContent = ''; w.classList.add('d-none'); }
  }

  function checkAllPending() {
    if (removingEverything()) {
      showWarning('All test cases are marked for removal. At least one must remain.');
    } else {
      clearWarning();
    }
  }

  document.addEventListener('click', function (e) {
    var removeBtn = e.target.closest('.tc-remove-btn');
    var undoBtn = e.target.closest('.tc-undo-btn');

    if (removeBtn) {
      var tcId = removeBtn.dataset.tcId;
      var ordinal = removeBtn.dataset.tcOrdinal;
      if (!confirm('Mark test case ' + ordinal + ' for removal on save?')) return;
      pendingRemovals.add(tcId);
      var row = document.getElementById('tc-' + tcId);
      row.classList.add('tc-pending-removal');
      removeBtn.classList.add('d-none');
      row.querySelector('.tc-undo-btn').classList.remove('d-none');
      syncHiddenInput();
      checkAllPending();
    }

    if (undoBtn) {
      var tcId2 = undoBtn.dataset.tcId;
      pendingRemovals.delete(tcId2);
      var row2 = document.getElementById('tc-' + tcId2);
      row2.classList.remove('tc-pending-removal');
      undoBtn.classList.add('d-none');
      row2.querySelector('.tc-remove-btn').classList.remove('d-none');
      syncHiddenInput();
      checkAllPending();
    }
  });

  if (form) {
    // Picking (or clearing) a validator source file flips whether an empty
    // test-case set is legal, so re-evaluate the warning as it changes.
    var validatorInput = form.elements ? form.elements['validator_source_file'] : null;
    if (validatorInput) validatorInput.addEventListener('change', checkAllPending);

    form.addEventListener('submit', function (e) {
      if (removingEverything()) {
        e.preventDefault();
        showWarning('At least one test case must remain. Undo a removal before saving.');
      }
    });
  }
})();
