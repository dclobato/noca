// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Shared pending-removal + undo model for the Web and Arena admin problem-edit
// pages. Rows are marked for removal client-side and only deleted when the
// problem form is saved (the ids ride a hidden input). The enclosing form is
// found via `form[data-tc-pending-form]`, falling back to `#edit-form`.
//
// Two collections use this: test cases (`tc_remove_ids`) and, on interactive
// problems, sample interactions (`si_remove_ids`). Removals are keyed by id, not
// ordinal, so a pending mark survives a drag-reorder of the same list.
//
// Draft edit flows may temporarily remove every row. Create, enable and submit
// gates enforce what a judgeable problem actually needs.

(function () {
  "use strict";

  var form = document.querySelector('form[data-tc-pending-form]') || document.getElementById('edit-form');

  function initPendingRemove(config) {
    var pendingRemovals = new Set();

    function syncHiddenInput() {
      var el = document.getElementById(config.hiddenInputId);
      if (el) el.value = Array.from(pendingRemovals).join(',');
      document.dispatchEvent(new CustomEvent('noca:problem-edit-changed'));
    }

    function clearWarning() {
      var w = document.getElementById(config.warningId);
      if (w) { w.textContent = ''; w.classList.add('d-none'); }
    }

    document.addEventListener('click', function (e) {
      var removeBtn = e.target.closest('.' + config.removeBtnClass);
      var undoBtn = e.target.closest('.' + config.undoBtnClass);

      if (removeBtn) {
        var id = removeBtn.dataset.rowId;
        var ordinal = removeBtn.dataset.rowOrdinal;
        if (!confirm('Mark ' + config.noun + ' ' + ordinal + ' for removal on save?')) return;
        pendingRemovals.add(id);
        var row = document.getElementById(config.rowPrefix + id);
        row.classList.add('tc-pending-removal');
        removeBtn.classList.add('d-none');
        row.querySelector('.' + config.undoBtnClass).classList.remove('d-none');
        syncHiddenInput();
        clearWarning();
      }

      if (undoBtn) {
        var undoId = undoBtn.dataset.rowId;
        pendingRemovals.delete(undoId);
        var undoRow = document.getElementById(config.rowPrefix + undoId);
        undoRow.classList.remove('tc-pending-removal');
        undoBtn.classList.add('d-none');
        undoRow.querySelector('.' + config.removeBtnClass).classList.remove('d-none');
        syncHiddenInput();
        clearWarning();
      }
    });

    if (form) form.addEventListener('submit', clearWarning);
  }

  initPendingRemove({
    removeBtnClass: 'tc-remove-btn',
    undoBtnClass: 'tc-undo-btn',
    rowPrefix: 'tc-',
    hiddenInputId: 'tc_remove_ids',
    warningId: 'tc-remove-warning',
    noun: 'test case'
  });

  initPendingRemove({
    removeBtnClass: 'si-remove-btn',
    undoBtnClass: 'si-undo-btn',
    rowPrefix: 'si-',
    hiddenInputId: 'si_remove_ids',
    warningId: 'si-remove-warning',
    noun: 'sample interaction'
  });
})();
