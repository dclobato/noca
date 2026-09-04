//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Reveal spectator transport: the *ordering* half of the ceremony client.
//
// It owns exactly one rule, and no rendering at all: the authoritative state is
// always fetched over HTTP, and the SSE stream is only ever a signal to fetch
// again. Valkey pub/sub is not replayable and the nudge carries no state, so a
// client that subscribed first and trusted event contents would show a ceremony
// that silently diverges from the store.
//
// Concretely:
//   1. fetch /reveal/state and hand it to onState;
//   2. only after that request settles successfully, construct the EventSource;
//   3. every `reveal_state_changed` triggers another authoritative fetch;
//   4. every `reveal_ready` after a (re)connect triggers one too, because events
//      published while the connection was down are gone forever;
//   5. a stream error replaces the EventSource with bounded backoff instead of
//      relying on browser-specific recovery from a terminal connection.
//
// The one event that breaks rule 3 is `reveal_media_cue`, and it does so because
// it is not a state signal at all: it is the operator asking the projector to
// raise or lower a team's media overlay, it changes nothing in the store, and
// fetching in response to it would buy a round trip per button press and return
// exactly the state already on screen. It is handed straight to `onMediaCue`.
//
// Phase 14's ceremony.js consumes this module (subscribing through onState) or
// replaces it wholesale; nothing here touches the DOM, so it stays testable
// headlessly with an injected fetch and EventSource. Exported as UMD:
// `window.RevealTransport` / `module.exports`.
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.RevealTransport = api;
  }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  var EVENT_STATE_CHANGED = "reveal_state_changed";
  var EVENT_READY = "reveal_ready";
  var EVENT_MEDIA_CUE = "reveal_media_cue";

  // Retry schedule for a failed state request, in milliseconds. A ceremony is a
  // live event: a projector that gave up after one 503 stays blank until someone
  // notices and reloads it, which is exactly the failure mode a reveal cannot
  // afford. The schedule backs off to a steady 5 s and then repeats, so a
  // recovering server is picked up promptly and a long outage costs one request
  // every five seconds. A `Retry-After` hint from the server (the store's own
  // 503 sends one) overrides the schedule when it asks for a longer wait.
  var RETRY_DELAYS_MS = [500, 1000, 2000, 5000];

  // Coalescing, stale-safe state fetcher: at most one request in flight plus one
  // queued, so a burst of nudges (an operator holding the step key) collapses to
  // a single follow-up instead of one fetch per event. A response is applied only
  // when it belongs to the newest completed request, so a slow earlier fetch can
  // never overwrite a newer ceremony state.
  function createStateFetcher(fetchState, onState, onError, schedule) {
    var seq = 0;
    var appliedSeq = 0;
    var inFlight = false;
    var queued = false;
    var attempts = 0;
    var retryHandle = null;
    var stopped = false;
    var timers = schedule || {
      setTimeout: function (fn, ms) {
        return setTimeout(fn, ms);
      },
      clearTimeout: function (handle) {
        clearTimeout(handle);
      },
    };

    function retryDelay(error) {
      var index = Math.min(Math.max(attempts - 1, 0), RETRY_DELAYS_MS.length - 1);
      var base = RETRY_DELAYS_MS[index];
      // Honor a server-sent Retry-After (seconds) when it is the longer wait:
      // the server knows better than the schedule how soon it can answer.
      var hint = error && typeof error.retryAfterSeconds === "number" ? error.retryAfterSeconds * 1000 : 0;
      return Math.max(base, hint);
    }

    function scheduleRetry(error) {
      if (stopped || retryHandle !== null) {
        return;
      }
      retryHandle = timers.setTimeout(function () {
        retryHandle = null;
        if (inFlight) {
          queued = true;
          return;
        }
        run();
      }, retryDelay(error));
    }

    function cancelRetry() {
      if (retryHandle !== null) {
        timers.clearTimeout(retryHandle);
        retryHandle = null;
      }
    }

    function run() {
      inFlight = true;
      var mySeq = ++seq;
      return Promise.resolve()
        .then(fetchState)
        .then(function (state) {
          cancelRetry();
          attempts = 0;
          if (mySeq > appliedSeq) {
            appliedSeq = mySeq;
            onState(state, mySeq);
          }
          return true;
        })
        .catch(function (error) {
          var retryable = isRetryableStateError(error);
          onError(error, retryable);
          attempts += 1;
          // Never leave the client stranded on a transient failure: an initial
          // 503 or a failed refetch after the last nudge would otherwise freeze
          // the projector for the rest of the ceremony.
          if (retryable && !queued) {
            scheduleRetry(error);
          }
          return false;
        })
        .then(function (ok) {
          inFlight = false;
          if (queued) {
            queued = false;
            return run();
          }
          return ok;
        });
    }

    return {
      // Resolves with the outcome of the request this call triggered (or of the
      // one it folded into), which is what lets `start()` wait for the initial
      // fetch before opening the stream.
      trigger: function () {
        if (stopped) {
          return Promise.resolve(false);
        }
        cancelRetry();
        if (inFlight) {
          queued = true;
          return Promise.resolve(false);
        }
        return run();
      },
      // Number of consecutive failures; zero once a request succeeds.
      failureCount: function () {
        return attempts;
      },
      stop: function () {
        stopped = true;
        queued = false;
        cancelRetry();
      },
    };
  }

  function isRetryableStateError(error) {
    // A rejected fetch has no HTTP status and represents a network failure.
    // Retry only the endpoint/proxy statuses that can recover without changing
    // the request. In particular, the state endpoint's corrupt-payload 500 is
    // deliberately permanent and must not become a public retry storm.
    if (!error || typeof error.status !== "number") {
      return true;
    }
    return error.status === 429 || error.status === 502 || error.status === 503 || error.status === 504;
  }

  // deps: { fetchState, EventSourceCtor, eventsUrl, onState, onError,
  //         onConnectionError, onMediaCue, schedule }
  function createRevealTransport(deps) {
    var notifyState = deps.onState || function () {};
    var onMediaCue = deps.onMediaCue || function () {};
    var onError = deps.onError || function () {};
    var onConnectionError = deps.onConnectionError || onError;
    var source = null;
    var stopped = false;
    var streamFailures = 0;
    var streamRetryHandle = null;
    var timers = deps.schedule || {
      setTimeout: function (fn, ms) {
        return setTimeout(fn, ms);
      },
      clearTimeout: function (handle) {
        clearTimeout(handle);
      },
    };

    function cancelStreamRetry() {
      if (streamRetryHandle !== null) {
        timers.clearTimeout(streamRetryHandle);
        streamRetryHandle = null;
      }
    }

    function scheduleStreamRetry() {
      if (stopped || streamRetryHandle !== null) {
        return;
      }
      var index = Math.min(streamFailures, RETRY_DELAYS_MS.length - 1);
      var delay = RETRY_DELAYS_MS[index];
      streamFailures += 1;
      streamRetryHandle = timers.setTimeout(function () {
        streamRetryHandle = null;
        openStream();
      }, delay);
    }

    // Any successful state application also (re)opens the stream. That is what
    // makes the retry loop complete: a `start()` whose first fetch failed still
    // ends up subscribed once a retry succeeds, instead of retrying forever with
    // no subscription behind it.
    function onState(state, seq) {
      notifyState(state, seq);
      openStream();
    }

    var fetcher = createStateFetcher(deps.fetchState, onState, onError, deps.schedule);

    function openStream() {
      if (stopped || source !== null || typeof deps.EventSourceCtor !== "function") {
        // Without EventSource support the ceremony still works: the caller may
        // drive refresh() itself. Nothing here silently degrades into polling.
        return;
      }
      var candidate;
      try {
        candidate = new deps.EventSourceCtor(deps.eventsUrl);
      } catch (error) {
        onConnectionError(error, true);
        scheduleStreamRetry();
        return;
      }
      source = candidate;
      candidate.addEventListener(EVENT_STATE_CHANGED, function () {
        if (source !== candidate) {
          return;
        }
        fetcher.trigger();
      });
      // Deliberately no fetcher.trigger() here: a cue carries its own complete
      // payload and moves no ceremony state, so refetching would be a wasted
      // round trip on every press of the operator's button. A malformed frame is
      // dropped rather than thrown, keeping one bad publish from tearing down a
      // projector's stream mid-ceremony.
      candidate.addEventListener(EVENT_MEDIA_CUE, function (event) {
        if (source !== candidate) {
          return;
        }
        var cue = null;
        try {
          cue = JSON.parse(event.data);
        } catch (error) {
          return;
        }
        if (cue && (cue.action === "show" || cue.action === "hide")) {
          onMediaCue(cue);
        }
      });
      // Reconcile on `reveal_ready`, NOT on `onopen`. The response headers — and
      // therefore `open` — are written before the server's Valkey subscription
      // exists, so a mutation published in that window would reach neither this
      // fetch nor the not-yet-live subscription, and pub/sub has no replay. The
      // server emits `reveal_ready` only once it is actually subscribed, which
      // makes this reconciliation strictly follow coverage on every connection
      // and every reconnect.
      candidate.addEventListener(EVENT_READY, function () {
        if (source !== candidate) {
          return;
        }
        streamFailures = 0;
        cancelStreamRetry();
        fetcher.trigger();
      });
      candidate.onerror = function (error) {
        if (stopped || source !== candidate) {
          return;
        }
        // A browser may retry a disconnected EventSource, but an HTTP failure
        // during server startup can also leave it permanently CLOSED. Own the
        // retry lifecycle so both outcomes recover the same way.
        candidate.close();
        source = null;
        onConnectionError(error, true);
        scheduleStreamRetry();
      };
    }

    return {
      // Fetch first, subscribe second — never the reverse. A failed initial
      // fetch is retried by the fetcher, and the stream opens on the first
      // success, so a server that is briefly unavailable at page load costs a
      // delay rather than a dead projector.
      start: function () {
        return fetcher.trigger().then(function (ok) {
          if (ok) {
            openStream();
          }
          return ok;
        });
      },
      refresh: function () {
        return fetcher.trigger();
      },
      stop: function () {
        stopped = true;
        fetcher.stop();
        cancelStreamRetry();
        if (source !== null) {
          source.close();
          source = null;
        }
      },
      isStreaming: function () {
        return source !== null;
      },
      failureCount: function () {
        return fetcher.failureCount();
      },
    };
  }

  return {
    EVENT_STATE_CHANGED: EVENT_STATE_CHANGED,
    EVENT_READY: EVENT_READY,
    EVENT_MEDIA_CUE: EVENT_MEDIA_CUE,
    RETRY_DELAYS_MS: RETRY_DELAYS_MS,
    createStateFetcher: createStateFetcher,
    createRevealTransport: createRevealTransport,
    isRetryableStateError: isRetryableStateError,
  };
});
