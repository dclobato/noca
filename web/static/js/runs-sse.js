// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

(function () {
  function parseVerdictEvent(rawData) {
    if (rawData === 'ping') return null;

    try {
      var payload = JSON.parse(rawData);
      if (!payload || payload.kind !== 'verdict-update') return null;
      return payload;
    } catch (err) {
      return null;
    }
  }

  function getWrapper() {
    return document.getElementById('runs-list-wrapper');
  }

  function wrapperAcceptsPE(wrapper) {
    return !!wrapper && wrapper.getAttribute('data-accept-pe') === 'true';
  }

  function getTopMarker(wrapper) {
    if (!wrapper) return null;
    var row = wrapper.querySelector('tbody tr[id]');
    if (!row) return null;
    return {
      submissionId: row.id || '',
      status: row.getAttribute('data-judgment-status') || '',
      autojudge: row.getAttribute('data-autojudge') || '',
      finalVerdict: row.getAttribute('data-final-verdict') || ''
    };
  }

  function showNotice(message) {
    var notice = document.getElementById('runs-sse-notice');
    if (!notice || !message) return;

    notice.textContent = message;
    notice.classList.remove('d-none');

    if (notice._hideTimer) {
      window.clearTimeout(notice._hideTimer);
    }
    notice._hideTimer = window.setTimeout(function () {
      notice.classList.add('d-none');
      notice.textContent = '';
    }, 6000);
  }

  function describeTopChange(previousMarker, nextMarker) {
    if (!previousMarker || !nextMarker) return '';

    if (previousMarker.submissionId !== nextMarker.submissionId) {
      if (nextMarker.finalVerdict) {
        return 'A newer judged submission is now at the top of the runs list.';
      }
      return 'A newer submission is now at the top of the runs list.';
    }

    if (!previousMarker.finalVerdict && nextMarker.finalVerdict) {
      return 'The top submission received final verdict ' + nextMarker.finalVerdict + '.';
    }

    if (previousMarker.autojudge !== nextMarker.autojudge && nextMarker.autojudge) {
      return 'The top submission received autojudge verdict ' + nextMarker.autojudge + '.';
    }

    if (previousMarker.status !== nextMarker.status && nextMarker.status === 'DONE') {
      return 'The top submission finished judging.';
    }

    return '';
  }

  function isAcceptedVerdict(verdict, acceptPE) {
    return verdict === 'AC' || (verdict === 'PE' && acceptPE);
  }

  function findAcceptedVisibleRow(wrapper, eventPayload) {
    if (!wrapper || !eventPayload || !eventPayload.team_id || !eventPayload.problem_id || !eventPayload.verdict) {
      return null;
    }

    var rows = wrapper.querySelectorAll('tbody tr[data-team-id][data-problem-id][data-final-verdict]');
    for (var i = 0; i < rows.length; i += 1) {
      var row = rows[i];
      if (row.getAttribute('data-team-id') !== eventPayload.team_id) continue;
      if (row.getAttribute('data-problem-id') !== eventPayload.problem_id) continue;
      if (row.getAttribute('data-final-verdict') !== eventPayload.verdict) continue;
      return row;
    }

    return null;
  }

  function dispatchAcceptedVisible(wrapper, eventPayload) {
    var acceptPE = wrapperAcceptsPE(wrapper);
    if (!eventPayload || !isAcceptedVerdict(eventPayload.verdict, acceptPE)) return;

    var row = findAcceptedVisibleRow(wrapper, eventPayload);
    if (!row) return;

    document.dispatchEvent(new CustomEvent('runs:accepted-visible', {
      detail: {
        submissionId: row.id || '',
        teamId: eventPayload.team_id,
        problemId: eventPayload.problem_id,
        verdict: eventPayload.verdict,
        updateKind: eventPayload.update_kind || null
      }
    }));
  }

  var wrapper = getWrapper();
  if (!wrapper) return;

  var sseUrl = wrapper.getAttribute('data-sse-url');
  if (!sseUrl || !window.EventSource) return;

  var es = new EventSource(sseUrl);
  var pendingTopMarker = null;
  var pendingEvents = [];
  var refreshInFlight = false;

  es.onmessage = function (e) {
    var parsedEvent = parseVerdictEvent(e.data);
    if (!parsedEvent) return;

    document.dispatchEvent(new CustomEvent('runs:verdict-received', { detail: parsedEvent }));

    var currentWrapper = getWrapper();
    if (!currentWrapper) return;

    pendingEvents.push(parsedEvent);
    if (refreshInFlight) return;

    pendingTopMarker = getTopMarker(currentWrapper);
    refreshInFlight = true;
    htmx.trigger(currentWrapper, 'verdict-update');
  };

  // Let the browser handle reconnection automatically via the built-in
  // EventSource retry mechanism.  Logging the error is sufficient; closing the
  // connection here would permanently stop SSE updates on transient failures.

  document.addEventListener('htmx:afterSwap', function (evt) {
    if (!evt.detail || !evt.detail.target || evt.detail.target.id !== 'runs-list-wrapper') return;

    var nextMarker = getTopMarker(evt.detail.target);
    var message = describeTopChange(pendingTopMarker, nextMarker);
    for (var i = 0; i < pendingEvents.length; i += 1) {
      dispatchAcceptedVisible(evt.detail.target, pendingEvents[i]);
    }
    pendingTopMarker = null;
    pendingEvents = [];
    refreshInFlight = false;
    if (message) {
      showNotice(message);
    }
  });
})();
