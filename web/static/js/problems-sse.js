// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Problem-list live refresh: on any verdict SSE message (reusing the Runs
// page's stream), re-fetches the problem card grid via htmx and diffs each
// card's own `data-viewer-status` before and after the swap. A problem that
// just became "solved" celebrates via the shared NocaConfetti burst (see
// shared/static/js/confetti-celebrate.js), called directly here rather than
// through a dedicated listener file -- Arena's own live-update scripts
// (profile-submissions-live.js, submission-detail-live.js) call
// NocaConfetti.celebrate() the same way, at the point of state change.
//
// The SSE payload itself is never inspected for verdict details: during a
// scoreboard freeze it is intentionally stripped down to
// {"kind": "verdict-update"} with no team/problem/verdict fields (see
// contest_runs_helpers._shape_sse_payload), so trusting it here would either
// miss real solves or -- worse -- leak a frozen one. Every non-ping message
// instead triggers an unconditional refresh, and the *server-rendered* DOM
// after the swap is the only source of truth, exactly like runs-sse.js
// already does for the Runs page.
(function () {
  function parseVerdictEvent(rawData) {
    if (rawData === 'ping') return null;
    try {
      var payload = JSON.parse(rawData);
      return payload && payload.kind === 'verdict-update' ? payload : null;
    } catch (err) {
      return null;
    }
  }

  function getWrapper() {
    return document.getElementById('problems-list');
  }

  function readStatuses(wrapper) {
    var statuses = {};
    if (!wrapper) return statuses;
    wrapper.querySelectorAll('[data-problem-id]').forEach(function (card) {
      statuses[card.getAttribute('data-problem-id')] = card.getAttribute('data-viewer-status') || '';
    });
    return statuses;
  }

  var wrapper = getWrapper();
  if (!wrapper) return;

  var sseUrl = wrapper.getAttribute('data-sse-url');
  if (!sseUrl || !window.EventSource) return;

  var pendingStatuses = null;
  var refreshInFlight = false;

  function refreshList() {
    var currentWrapper = getWrapper();
    if (!currentWrapper || refreshInFlight) return;

    pendingStatuses = readStatuses(currentWrapper);
    refreshInFlight = true;
    htmx.trigger(currentWrapper, 'verdict-update');
  }

  function onVerdictMessage(e) {
    if (!parseVerdictEvent(e.data)) return;
    refreshList();
  }

  // Same contract as runs-sse.js: the browser retries transient failures,
  // NocaSse surfaces and retries a refused connection (429 from the SSE lease).
  if (window.NocaSse) {
    NocaSse.open(sseUrl, { onMessage: onVerdictMessage, onRecovered: refreshList });
  } else {
    new EventSource(sseUrl).onmessage = onVerdictMessage;
  }

  document.addEventListener('htmx:afterSwap', function (evt) {
    if (!evt.detail || !evt.detail.target || evt.detail.target.id !== 'problems-list') return;

    var nextStatuses = readStatuses(evt.detail.target);
    var previousStatuses = pendingStatuses || {};
    if (window.NocaConfetti) {
      Object.keys(nextStatuses).forEach(function (problemId) {
        if (nextStatuses[problemId] === 'solved' && previousStatuses[problemId] !== 'solved') {
          window.NocaConfetti.celebrate(problemId);
        }
      });
    }
    pendingStatuses = null;
    refreshInFlight = false;
  });
})();
