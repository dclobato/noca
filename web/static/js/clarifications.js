// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

(function () {
  var detailModal = document.getElementById('clarificationDetailModal');
  var bsModal = detailModal ? new bootstrap.Modal(detailModal) : null;
  if (detailModal) {
    detailModal.addEventListener('show.bs.modal', function (event) {
      var row = event.relatedTarget;
      document.getElementById('modal-problem').textContent = row.getAttribute('data-problem') || '—';
      document.getElementById('modal-question').textContent = row.getAttribute('data-question') || '';
      document.getElementById('modal-answer').textContent = row.getAttribute('data-answer') || '—';
      var vis = row.getAttribute('data-visibility');
      var visEl = document.getElementById('modal-visibility');
      if (visEl) {
        visEl.innerHTML = vis === 'global'
          ? '<span class="badge bg-info text-dark">Global</span>'
          : '<span class="badge bg-secondary">Private</span>';
      }
    });
  }

  // Make the whole clarification row open the detail modal, using the row's
  // own data-* attributes; clicks on other row controls (answer, hide,
  // release lock) keep their own action instead of opening the modal.
  var INTERACTIVE = 'a, button, input, select, textarea, label';

  function openRowDetail(row) {
    if (!bsModal) return;
    bsModal.show(row);
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

  function acknowledgeRenderedAnswers() {
    var wrapper = document.getElementById('clarifications-list-wrapper');
    if (!wrapper) return;
    var answerReadUrl = wrapper.getAttribute('data-answer-read-url');
    var unreadRows = wrapper.querySelectorAll('[data-unread-answer="true"]');
    if (!answerReadUrl || unreadRows.length === 0) return;

    var body = new URLSearchParams();
    unreadRows.forEach(function (row) {
      body.append('clarification_ids', row.id);
    });
    fetch(answerReadUrl, {
      method: 'POST',
      body: body,
      credentials: 'same-origin'
    }).then(function (response) {
      if (!response.ok) return;
      unreadRows.forEach(function (row) {
        row.setAttribute('data-unread-answer', 'acknowledged');
      });
    }).catch(function () {
      // Keep the rows unread so a later page load or HTMX refresh retries.
    });
  }

  acknowledgeRenderedAnswers();
  document.body.addEventListener('htmx:afterSwap', acknowledgeRenderedAnswers);
})();
