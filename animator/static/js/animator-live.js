//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Live-connection controller for the animator scoreboard. Owns exactly one
// EventSource, one snapshot RefreshCoordinator, and the Live/Reconnecting/Polling
// state machine. Every dependency (EventSource ctor, fetch, timers, apply) is
// injected so the whole wiring is exercised headlessly. Exported as UMD:
// `window.AnimatorLive` / `module.exports`.
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.AnimatorLive = api;
  }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  // Consecutive SSE errors before giving up on the stream and polling instead.
  var POLL_AFTER_FAILURES = 2;

  var STATUS_LIVE = "live";
  var STATUS_RECONNECTING = "reconnecting";
  var STATUS_POLLING = "polling";

  // Coalescing, stale-safe snapshot fetcher: at most one request in flight plus
  // one queued (a burst collapses to a single follow-up). Every response is
  // ordered by its request sequence and its server `version`; a response is
  // applied only when it is the newest completed request *and* its version is not
  // older than the last applied snapshot, so a late-finishing older fetch or an
  // out-of-order version is dropped.
  function createRefreshCoordinator(fetchSnapshot, applySnapshot) {
    var seq = 0;
    var appliedSeq = 0;
    var appliedVersion = null;
    var inFlight = false;
    var queued = false;

    function versionOlder(candidate) {
      // ISO-8601 UTC strings compare lexicographically in chronological order.
      return appliedVersion !== null && candidate !== undefined && candidate < appliedVersion;
    }

    function run() {
      inFlight = true;
      var mySeq = ++seq;
      Promise.resolve()
        .then(fetchSnapshot)
        .then(function (snapshot) {
          var version = snapshot ? snapshot.version : undefined;
          if (mySeq > appliedSeq && !versionOlder(version)) {
            appliedSeq = mySeq;
            if (version !== undefined) {
              appliedVersion = version;
            }
            applySnapshot(snapshot, mySeq);
          }
        })
        .catch(function () {
          // Swallow transient fetch errors; the next event or poll retries.
        })
        .then(function () {
          inFlight = false;
          if (queued) {
            queued = false;
            run();
          }
        });
      return mySeq;
    }

    function trigger() {
      if (inFlight) {
        queued = true;
        return seq + 1;
      }
      return run();
    }

    return { trigger: trigger };
  }

  // Hold submission-cell flashes until the refresh scheduled by that submission
  // has been applied. This prevents an older request that was already in flight
  // from consuming a newer flash request before the pending cell is visible.
  function createPendingFlashQueue(options) {
    var requests = {};

    function enqueue(data, refreshSequence) {
      if (
        !data ||
        data.team_id === undefined ||
        data.problem_id === undefined ||
        typeof refreshSequence !== "number"
      ) {
        return;
      }
      var key = options.cellKey(data.team_id, data.problem_id);
      var existing = requests[key];
      requests[key] = {
        teamId: data.team_id,
        problemId: data.problem_id,
        minimumSequence: existing
          ? Math.max(existing.minimumSequence, refreshSequence)
          : refreshSequence,
      };
    }

    function apply(snapshot, refreshSequence) {
      if (typeof refreshSequence !== "number") {
        return;
      }
      var visiblePending = {};
      var entries = snapshot && Array.isArray(snapshot.pending_submissions) ? snapshot.pending_submissions : [];
      entries.forEach(function (entry) {
        if (entry && entry.team_id !== undefined && entry.problem_id !== undefined) {
          visiblePending[options.cellKey(entry.team_id, entry.problem_id)] = true;
        }
      });
      Object.keys(requests).forEach(function (key) {
        var request = requests[key];
        if (request.minimumSequence > refreshSequence) {
          return;
        }
        if (visiblePending[key]) {
          options.flashCell(request.teamId, request.problemId);
        }
        delete requests[key];
      });
    }

    return {
      enqueue: enqueue,
      apply: apply,
      _pendingCount: function () {
        return Object.keys(requests).length;
      },
    };
  }

  // The connection lifecycle. `deps`:
  //   EventSourceCtor: constructor or null/undefined when unsupported
  //   eventsUrl: SSE endpoint
  //   coordinator: { trigger }
  //   startPolling(pollOnce) / stopPolling(): own the single fallback interval
  //   setStatus(status): non-blocking status surface
  //   onSubmission(data, refreshSequence): optional; called with the parsed
  //     `submission` payload and the refresh generation scheduled for it
  //   onVerdict(data): optional; called with the parsed `verdict` payload
  function createConnectionController(deps) {
    var source = null;
    var failures = 0;
    var polling = false;
    var closed = false;

    function sourceIsClosed(candidate) {
      return (
        candidate &&
        deps.EventSourceCtor &&
        deps.EventSourceCtor.CLOSED !== undefined &&
        candidate.readyState === deps.EventSourceCtor.CLOSED
      );
    }

    function discardSource() {
      if (source) {
        source.close();
        source = null;
      }
    }

    function pollOnce() {
      if (closed || !polling) {
        return;
      }
      deps.coordinator.trigger();
      // A non-200 response or invalid event-stream response can leave a native
      // EventSource permanently CLOSED. Replace it explicitly; polling success
      // alone cannot cause that terminal object to emit `open`.
      if (sourceIsClosed(source)) {
        discardSource();
        openSource();
      }
    }

    function beginPolling() {
      if (!polling) {
        polling = true;
        deps.startPolling(pollOnce);
      }
      deps.setStatus(STATUS_POLLING);
    }

    function endPolling() {
      if (polling) {
        polling = false;
        deps.stopPolling();
      }
    }

    function onOpen(candidate) {
      if (closed || source !== candidate) {
        return;
      }
      // A fresh open — first connect or recovery — reconciles immediately: the
      // stream has no replay, so any update missed while down is fetched now.
      failures = 0;
      endPolling();
      deps.setStatus(STATUS_LIVE);
      deps.coordinator.trigger();
    }

    function onError(candidate) {
      if (closed || source !== candidate) {
        return;
      }
      failures += 1;
      if (sourceIsClosed(candidate) || failures >= POLL_AFTER_FAILURES) {
        beginPolling();
      } else {
        deps.setStatus(STATUS_RECONNECTING);
      }
    }

    function onRefresh() {
      // Only the authoritative refresh triggers a fetch; `verdict` and
      // `timer_tick` never do (Phase 07 emits a refresh after every verdict).
      deps.coordinator.trigger();
    }

    function parsePayload(event) {
      try {
        return JSON.parse(event.data);
      } catch (err) {
        return null;
      }
    }

    function onSubmission(event) {
      // A new-submission nudge: schedule the authoritative refresh, then hand
      // its generation to the caller so an older in-flight response cannot
      // consume the cell's pending flash.
      var data = parsePayload(event);
      if (!data) {
        return;
      }
      var refreshSequence = deps.coordinator.trigger();
      if (deps.onSubmission) {
        deps.onSubmission(data, refreshSequence);
      }
    }

    function onVerdict(event) {
      var data = parsePayload(event);
      if (data && deps.onVerdict) {
        deps.onVerdict(data);
      }
    }

    function openSource() {
      var candidate = new deps.EventSourceCtor(deps.eventsUrl);
      source = candidate;
      candidate.onopen = function () {
        onOpen(candidate);
      };
      candidate.onerror = function () {
        onError(candidate);
      };
      candidate.addEventListener("scoreboard_refresh", onRefresh);
      candidate.addEventListener("submission", onSubmission);
      // Verdict detail never fetches; the paired scoreboard_refresh does.
      candidate.addEventListener("verdict", onVerdict);
      candidate.addEventListener("timer_tick", function () {});
    }

    function connect() {
      if (!deps.EventSourceCtor) {
        // No SSE support in this browser: poll from the start.
        beginPolling();
        deps.coordinator.trigger();
        return;
      }
      openSource();
    }

    function close() {
      closed = true;
      endPolling();
      discardSource();
    }

    // Re-establish live updates after a back/forward-cache restore: the browser
    // suspends (and typically drops) the old EventSource without re-running page
    // init, so a fresh connection is opened and its `open` reconciles the gap.
    function reopen() {
      closed = false;
      failures = 0;
      endPolling();
      discardSource();
      connect();
    }

    return {
      connect: connect,
      close: close,
      reopen: reopen,
      refreshNow: function () {
        deps.coordinator.trigger();
      },
      _state: function () {
        return { failures: failures, polling: polling, closed: closed };
      },
    };
  }

  return {
    POLL_AFTER_FAILURES: POLL_AFTER_FAILURES,
    STATUS_LIVE: STATUS_LIVE,
    STATUS_RECONNECTING: STATUS_RECONNECTING,
    STATUS_POLLING: STATUS_POLLING,
    createRefreshCoordinator: createRefreshCoordinator,
    createPendingFlashQueue: createPendingFlashQueue,
    createConnectionController: createConnectionController,
  };
});
