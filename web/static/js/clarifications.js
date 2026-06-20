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
})();