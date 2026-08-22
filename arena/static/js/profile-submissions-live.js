// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Realtime updates for the Arena profile submissions tab. The shared
// NocaSubmissionStatusWatcher owns the SSE/poll/reconcile plumbing; this file
// supplies only how a snapshot row updates its table row: verdict/status badge,
// runtime cell, and confetti when a watched submission becomes AC.

(function () {
  'use strict';

  var section = document.querySelector('.arena-progress-list[data-status-url]');
  if (!section) return;
  if (!window.NocaSubmissionStatusWatcher) return;

  // submission_id -> <tr> for rows still being watched (non-final at last check).
  var watched = new Map();
  document.querySelectorAll('tr[data-submission-id]').forEach(function (tr) {
    if (tr.dataset.final !== 'true') {
      watched.set(tr.dataset.submissionId, tr);
    }
  });
  if (watched.size === 0) return;

  function escapeHtml(value) {
    var div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
  }

  function badgeHtml(row) {
    if (row.verdict) {
      var cls = row.verdict_badge_class || 'bg-secondary';
      var label = row.verdict_label || row.verdict;
      return '<span class="badge ' + cls + '">' + escapeHtml(label) + '</span>';
    }
    if (row.status) {
      return '<span class="badge bg-secondary font-monospace">'
        + escapeHtml(row.status) + '</span>';
    }
    return '<span class="text-muted">&mdash;</span>';
  }

  function applyRow(row) {
    var tr = watched.get(row.submission_id);
    if (!tr) return false;

    var badge = tr.querySelector('.js-verdict-badge');
    if (badge) {
      badge.innerHTML = badgeHtml(row);
    }
    var runtime = tr.querySelector('.js-submission-runtime');
    if (runtime) {
      runtime.innerHTML = row.max_wall_time_ms != null
        ? escapeHtml(row.max_wall_time_ms) + ' ms'
        : '&mdash;';
    }

    if (!row.is_final) return true;

    if (row.verdict === 'AC' && window.NocaConfetti) {
      window.NocaConfetti.celebrate(row.submission_id);
    }
    tr.dataset.final = 'true';
    watched.delete(row.submission_id);
    return false;
  }

  window.NocaSubmissionStatusWatcher.watch({
    statusUrl: section.dataset.statusUrl,
    eventsUrl: section.dataset.eventsUrl,
    ids: Array.from(watched.keys()),
    onSnapshot: applyRow
  });
})();
