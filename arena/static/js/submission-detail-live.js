// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Live updates for one pending, user-owned Arena submission. The shared
// NocaSubmissionStatusWatcher owns the SSE/poll/reconcile plumbing; this file
// supplies only how a snapshot renders the verdict summary card, the runtime,
// and the refetched result-card fragment.

(function () {
  'use strict';

  var root = document.querySelector('[data-submission-detail-live]');
  if (!root) return;
  if (!window.NocaSubmissionStatusWatcher) return;

  var submissionId = root.dataset.submissionId;
  var statusUrl = root.dataset.statusUrl;
  var eventsUrl = root.dataset.eventsUrl;
  if (!submissionId || !statusUrl || !eventsUrl) return;

  var finalCardRefreshStarted = false;

  var summary = root.querySelector('[data-live-verdict-summary]');
  var verdictCode = root.querySelector('[data-live-verdict-code]');
  var verdictLabel = root.querySelector('[data-live-verdict-label]');
  var verdictStatus = root.querySelector('[data-live-verdict-status]');
  var wallTime = root.querySelector('[data-live-wall-time]');

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
    if (!submission || submission.submission_id !== submissionId) return true;

    if (submission.is_final) {
      updateFinalState(submission);
    } else {
      updatePendingState(submission);
    }
    updateWallTime(submission);

    if (!submission.is_final) return true;

    refreshFinalResultCard();
    if (submission.verdict === 'AC' && window.NocaConfetti) {
      window.NocaConfetti.celebrate(submissionId);
    }
    return false;
  }

  window.NocaSubmissionStatusWatcher.watch({
    statusUrl: statusUrl,
    eventsUrl: eventsUrl,
    ids: [submissionId],
    onSnapshot: applySnapshot
  });
})();
