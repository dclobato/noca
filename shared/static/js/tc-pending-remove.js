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

(function () {
  var pendingRemovals = new Set();

  function syncHiddenInput() {
    var el = document.getElementById('tc_remove_ids');
    if (el) el.value = Array.from(pendingRemovals).join(',');
    document.dispatchEvent(new CustomEvent('noca:problem-edit-changed'));
  }

  function totalRows() {
    return document.querySelectorAll('.tc-row').length;
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
    if (pendingRemovals.size > 0 && pendingRemovals.size >= totalRows()) {
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

  var form = document.querySelector('form[data-tc-pending-form]') || document.getElementById('edit-form');
  if (form) {
    form.addEventListener('submit', function (e) {
      if (pendingRemovals.size > 0 && pendingRemovals.size >= totalRows()) {
        e.preventDefault();
        showWarning('At least one test case must remain. Undo a removal before saving.');
      }
    });
  }
})();
