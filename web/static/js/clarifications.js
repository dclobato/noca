// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

(function () {
  var detailModal = document.getElementById('clarificationDetailModal');
  if (detailModal) {
    detailModal.addEventListener('show.bs.modal', function (event) {
      var btn = event.relatedTarget;
      document.getElementById('modal-problem').textContent = btn.getAttribute('data-problem') || '—';
      document.getElementById('modal-question').textContent = btn.getAttribute('data-question') || '';
      document.getElementById('modal-answer').textContent = btn.getAttribute('data-answer') || '—';
      var vis = btn.getAttribute('data-visibility');
      var visEl = document.getElementById('modal-visibility');
      if (visEl) {
        visEl.innerHTML = vis === 'global'
          ? '<span class="badge bg-info text-dark">Global</span>'
          : '<span class="badge bg-secondary">Private</span>';
      }
    });
  }

  // Make the whole clarification row open the detail modal, deferring to the
  // eye button's own data-* attributes; clicks on other row controls (answer,
  // hide, release lock) keep their own action instead of opening the modal.
  var INTERACTIVE = 'a, button, input, select, textarea, label';

  function openRowDetail(row) {
    var detailBtn = row.querySelector('[data-bs-toggle="modal"]');
    if (detailBtn) detailBtn.click();
  }

  function handleRowActivate(event) {
    var row = event.target.closest('tr[data-clarification-row]');
    if (!row) return;
    if (event.target.closest(INTERACTIVE)) return;
    openRowDetail(row);
  }

  document.body.addEventListener('click', handleRowActivate);
  document.body.addEventListener('keydown', function (event) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    var row = event.target.closest('tr[data-clarification-row]');
    if (!row) return;
    if (event.target.closest(INTERACTIVE)) return;
    event.preventDefault();
    openRowDetail(row);
  });
})();