// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Shared submission-status watcher for the Arena pages that follow pending
// submissions live. Both consumers speak the same protocol against the same two
// endpoints (arena_user_submissions_status / _events): watch a set of submission
// ids, refetch the authoritative JSON snapshot on an owner-scoped SSE `refresh`
// ping, and fall back to a low-frequency poll. This module owns the whole
// SSE/poll/debounce/in-flight/teardown machinery so the two pages cannot drift;
// each page supplies only how a snapshot row updates its own DOM, via
// NocaSubmissionStatusWatcher.watch(options).

(function () {
  'use strict';

  // Fallback poll cadence while submissions are still pending. The SSE ping is
  // the fast path; this also surfaces intermediate states (QUEUED/DISPATCHED/
  // JUDGING) within one interval, since those publish no verdict event.
  var DEFAULT_POLL_MS = 2500;
  var DEFAULT_DEBOUNCE_MS = 250;

  // options:
  //   statusUrl   required  snapshot endpoint (arena_user_submissions_status)
  //   eventsUrl   required  SSE endpoint (arena_user_submissions_events)
  //   ids         required  submission ids to watch
  //   onSnapshot  required  (row) => boolean; an explicit `false` drops the id
  //                         (the page finalized its own DOM), anything else
  //                         keeps watching it
  //   pollMs      optional  default 2500
  //   debounceMs  optional  default 250
  // Returns { stop }. The snapshot endpoint is the sole data source: no verdict
  // data ever arrives over the SSE channel, only `refresh` pings.
  function watch(options) {
    var statusUrl = options.statusUrl;
    var eventsUrl = options.eventsUrl;
    var onSnapshot = options.onSnapshot;
    var pollMs = typeof options.pollMs === 'number' ? options.pollMs : DEFAULT_POLL_MS;
    var debounceMs = typeof options.debounceMs === 'number'
      ? options.debounceMs
      : DEFAULT_DEBOUNCE_MS;

    var watched = new Set(options.ids || []);
    if (!statusUrl || !eventsUrl || typeof onSnapshot !== 'function' || watched.size === 0) {
      return { stop: function () {} };
    }

    var source = null;
    var pollTimer = null;
    var debounceTimer = null;
    var controller = null;
    var reconciling = false;
    var stopped = false;

    function endpoint(url) {
      var query = new URLSearchParams({ ids: Array.from(watched).join(',') });
      return url + '?' + query.toString();
    }

    function teardown() {
      stopped = true;
      if (source) {
        source.close();
        source = null;
      }
      if (pollTimer !== null) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
      if (debounceTimer !== null) {
        clearTimeout(debounceTimer);
        debounceTimer = null;
      }
      if (controller) {
        // Cancel a fetch that is already awaiting, so it cannot resume after
        // teardown and drive the page's DOM callbacks.
        controller.abort();
        controller = null;
      }
    }

    async function reconcile() {
      if (stopped || reconciling) return;
      if (watched.size === 0) {
        teardown();
        return;
      }
      reconciling = true;
      var request = typeof AbortController === 'undefined' ? null : new AbortController();
      controller = request;

      try {
        var init = { headers: { 'Accept': 'application/json' } };
        if (request) init.signal = request.signal;

        var response = await fetch(endpoint(statusUrl), init);
        if (stopped) return;
        if (response.status === 401 || response.status === 403) {
          // Session expired or revoked: stop streaming and polling instead of
          // reconnecting and re-polling (and re-logging) indefinitely.
          teardown();
          return;
        }
        if (!response.ok) throw new Error('HTTP ' + response.status);

        var data = await response.json();
        if (stopped) return;

        (data.submissions || []).forEach(function (row) {
          if (stopped) return;
          if (onSnapshot(row) === false) {
            watched.delete(row.submission_id);
          }
        });
      } catch (err) {
        if (!err || err.name !== 'AbortError') {
          console.error('Submission status refresh failed', err);
        }
        return;
      } finally {
        reconciling = false;
        if (controller === request) controller = null;
      }

      if (watched.size === 0) {
        teardown();
      }
    }

    function scheduleReconcile() {
      if (stopped || debounceTimer !== null) return;
      debounceTimer = setTimeout(function () {
        debounceTimer = null;
        reconcile();
      }, debounceMs);
    }

    function connect() {
      if (typeof EventSource === 'undefined') {
        // No SSE available: the poll below is the only channel, so reconcile
        // once immediately instead of waiting a full interval.
        reconcile();
        return;
      }
      source = new EventSource(endpoint(eventsUrl));
      // Reconcile on (re)connect to recover any finalization missed while the
      // pub/sub stream was unsubscribed; the server emits an initial ping too.
      source.onopen = scheduleReconcile;
      source.onmessage = function (event) {
        if (event.data === 'refresh') {
          scheduleReconcile();
        }
      };
    }

    window.addEventListener('pagehide', teardown, { once: true });
    connect();
    pollTimer = setInterval(reconcile, pollMs);

    return { stop: teardown };
  }

  window.NocaSubmissionStatusWatcher = { watch: watch };
})();
