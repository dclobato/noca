// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Realtime updates for the Arena profile submissions tab. Watches the visible
// non-final submission rows; on a user-scoped SSE `refresh` ping (or a periodic
// fallback poll) it refetches the authoritative status snapshot, updates the
// verdict/status/runtime cells in place, and fires confetti when a watched
// submission becomes AC. The snapshot endpoint is the sole data source.

(function () {
  'use strict';

  var section = document.querySelector('.arena-progress-list[data-status-url]');
  if (!section) return;

  var statusUrl = section.dataset.statusUrl;
  var eventsUrl = section.dataset.eventsUrl;

  // submission_id -> <tr> for rows still being watched (non-final at last check).
  var watched = new Map();
  document.querySelectorAll('tr[data-submission-id]').forEach(function (tr) {
    if (tr.dataset.final !== 'true') {
      watched.set(tr.dataset.submissionId, tr);
    }
  });
  if (watched.size === 0) return;

  var source = null;
  var pollTimer = null;
  var refetchTimer = null;
  // Fallback poll cadence while rows are still pending. The SSE ping is the fast
  // path; this also surfaces intermediate states (QUEUED/DISPATCHED/JUDGING),
  // which are not published as verdict events, within one interval.
  var POLL_MS = 2500;
  var DEBOUNCE_MS = 250;

  function watchedIds() {
    return Array.from(watched.keys());
  }

  function teardown() {
    if (source) {
      source.close();
      source = null;
    }
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function badgeHtml(row) {
    if (row.verdict) {
      var cls = row.verdict_badge_class || 'bg-secondary';
      var label = row.verdict_label || row.verdict;
      return '<span class="badge ' + cls + '">' + escapeHtml(label) + '</span>';
    }
    if (row.status) {
      return '<span class="badge bg-secondary font-monospace">' + escapeHtml(row.status) + '</span>';
    }
    return '<span class="text-muted">&mdash;</span>';
  }

  function escapeHtml(value) {
    var div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
  }

  function applyRow(row) {
    var tr = watched.get(row.submission_id);
    if (!tr) return;

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

    if (row.is_final) {
      if (row.verdict === 'AC' && window.NocaConfetti) {
        window.NocaConfetti.celebrate(row.submission_id);
      }
      tr.dataset.final = 'true';
      watched.delete(row.submission_id);
    }
  }

  async function reconcile() {
    if (watched.size === 0) {
      teardown();
      return;
    }
    var query = new URLSearchParams({ ids: watchedIds().join(',') });
    try {
      var response = await fetch(statusUrl + '?' + query.toString(), {
        headers: { 'Accept': 'application/json' }
      });
      if (response.status === 401 || response.status === 403) {
        // Session expired or revoked: stop streaming and polling instead of
        // reconnecting and re-polling (and re-logging) indefinitely.
        teardown();
        return;
      }
      if (!response.ok) throw new Error('HTTP ' + response.status);
      var data = await response.json();
      (data.submissions || []).forEach(applyRow);
    } catch (err) {
      console.error('Submission status refresh failed', err);
      return;
    }
    if (watched.size === 0) {
      teardown();
    }
  }

  function scheduleReconcile() {
    if (refetchTimer) return;
    refetchTimer = setTimeout(function () {
      refetchTimer = null;
      reconcile();
    }, DEBOUNCE_MS);
  }

  function connect() {
    var query = new URLSearchParams({ ids: watchedIds().join(',') });
    source = new EventSource(eventsUrl + '?' + query.toString());
    // Reconcile on (re)connect to recover any finalization missed while the
    // pub/sub stream was unsubscribed; the server emits an initial ping too.
    source.onopen = scheduleReconcile;
    source.onmessage = function (event) {
      if (event.data === 'refresh') {
        scheduleReconcile();
      }
    };
  }

  connect();
  pollTimer = setInterval(reconcile, POLL_MS);
})();
