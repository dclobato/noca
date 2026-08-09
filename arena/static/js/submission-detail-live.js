// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Live updates for one pending, user-owned Arena submission. The owner-scoped
// SSE endpoint is the fast finalization signal; an authoritative snapshot fetch
// renders every state. A low-frequency poll also surfaces QUEUED, DISPATCHED,
// and JUDGING because those intermediate states do not publish SSE events.

(function () {
  'use strict';

  var root = document.querySelector('[data-submission-detail-live]');
  if (!root) return;

  var submissionId = root.dataset.submissionId;
  var statusUrl = root.dataset.statusUrl;
  var eventsUrl = root.dataset.eventsUrl;
  if (!submissionId || !statusUrl || !eventsUrl) return;

  var source = null;
  var pollTimer = null;
  var reconciling = false;
  var resolved = false;
  var finalCardRefreshStarted = false;
  var sseRefreshObserved = false;
  var POLL_MS = 2500;

  var summary = root.querySelector('[data-live-verdict-summary]');
  var verdictCode = root.querySelector('[data-live-verdict-code]');
  var verdictLabel = root.querySelector('[data-live-verdict-label]');
  var verdictStatus = root.querySelector('[data-live-verdict-status]');
  var wallTime = root.querySelector('[data-live-wall-time]');

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

  function endpoint(url) {
    var query = new URLSearchParams({ ids: submissionId });
    return url + '?' + query.toString();
  }

  function matchingSubmission(payload) {
    return (payload.submissions || []).find(function (submission) {
      return submission.submission_id === submissionId;
    });
  }

  function isUnauthorized(response) {
    return response.status === 401 || response.status === 403;
  }

  function summaryStateClass(verdict) {
    if (verdict === 'AC') return 'is-accepted';
    if (verdict === 'WA' || verdict === 'PE') return 'is-rejected';
    if (['TLE', 'MLE', 'OLE', 'RE'].includes(verdict)) return 'is-warning';
    return verdict ? 'is-neutral' : 'is-pending';
  }

  function updateSummaryState(verdict) {
    if (!summary) return;
    summary.classList.remove(
      'is-accepted',
      'is-rejected',
      'is-warning',
      'is-neutral',
      'is-pending'
    );
    summary.classList.add(summaryStateClass(verdict));
  }

  function updatePendingState(submission) {
    updateSummaryState(null);
    if (verdictLabel) verdictLabel.textContent = 'Pending judgment';
    if (verdictStatus) {
      verdictStatus.textContent = 'Current status: ' + (submission.status || 'QUEUED');
    }
  }

  function updateFinalState(submission) {
    updateSummaryState(submission.verdict);

    if (verdictCode && submission.verdict) {
      var badge = document.createElement('span');
      badge.className = 'badge '
        + (submission.verdict_badge_class || 'bg-secondary')
        + ' font-monospace';
      badge.textContent = submission.verdict;
      verdictCode.replaceChildren(badge);
    }
    if (verdictLabel) {
      verdictLabel.textContent = submission.verdict_label
        || submission.verdict
        || 'Judgment failed';
    }
    if (verdictStatus) {
      verdictStatus.remove();
      verdictStatus = null;
    }
  }

  function updateWallTime(submission) {
    if (!wallTime) return;
    wallTime.textContent = submission.max_wall_time_ms == null
      ? '\u2014'
      : submission.max_wall_time_ms + ' ms';
  }

  async function refreshFinalResultCard() {
    if (finalCardRefreshStarted) return;
    finalCardRefreshStarted = true;

    try {
      var response = await fetch(window.location.href, {
        headers: { 'Accept': 'text/html' }
      });
      if (!response.ok) throw new Error('HTTP ' + response.status);

      var renderedPage = new DOMParser().parseFromString(await response.text(), 'text/html');
      var currentCard = document.querySelector('[data-submission-result-card]');
      var refreshedCard = renderedPage.querySelector('[data-submission-result-card]');
      if (currentCard && refreshedCard) {
        currentCard.replaceWith(refreshedCard);
      }
    } catch (error) {
      console.error('Submission result card refresh failed', error);
    }
  }

  function applySnapshot(submission) {
    if (!submission) return;

    if (submission.is_final) {
      updateFinalState(submission);
    } else {
      updatePendingState(submission);
    }
    updateWallTime(submission);

    if (!submission.is_final) return;
    refreshFinalResultCard();
    if (!sseRefreshObserved) return;

    resolved = true;
    teardown();
    if (submission.verdict === 'AC' && window.NocaConfetti) {
      window.NocaConfetti.celebrate(submissionId);
    }
  }

  async function reconcile() {
    if (reconciling || resolved) return;
    reconciling = true;

    try {
      var response = await fetch(endpoint(statusUrl), {
        headers: { 'Accept': 'application/json' }
      });
      if (isUnauthorized(response)) {
        teardown();
        return;
      }
      if (!response.ok) throw new Error('HTTP ' + response.status);

      applySnapshot(matchingSubmission(await response.json()));
    } catch (error) {
      console.error('Submission detail status refresh failed', error);
    } finally {
      reconciling = false;
    }
  }

  function handleRefreshMessage(event) {
    if (event.data === 'refresh') {
      sseRefreshObserved = true;
      reconcile();
    }
  }

  function connect() {
    source = new EventSource(endpoint(eventsUrl));
    source.onopen = reconcile;
    source.onmessage = handleRefreshMessage;
  }

  window.addEventListener('pagehide', teardown, { once: true });
  if (typeof EventSource === 'undefined') {
    reconcile();
  } else {
    connect();
  }
  pollTimer = setInterval(reconcile, POLL_MS);
})();
