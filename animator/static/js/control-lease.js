//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Ephemeral controller-lease transport. Exported as UMD:
// `window.AnimatorControlLease` / `module.exports`.
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.AnimatorControlLease = api;
  }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  var DEFAULT_TTL_SECONDS = 45;
  var DEFAULT_HEARTBEAT_SECONDS = 10;
  var CONTROLLER_HEADER = "X-Animator-Controller-Id";
  // A failed heartbeat that carries no stated ownership answer (a network
  // blip, a 503) is retried inside the lease's remaining TTL instead of
  // dropping command authority on the first miss — this is what makes the
  // server-side "TTL >= 3x heartbeat" validator worth having. Two consecutive
  // misses (~TTL/3 apart) exhaust the benefit of the doubt.
  var MAX_MISSED_HEARTBEATS = 2;

  function fallbackId() {
    return (
      "panel-" +
      Date.now().toString(36) +
      "-" +
      Math.random().toString(36).slice(2, 12) +
      Math.random().toString(36).slice(2, 12)
    );
  }

  function makeControllerId(randomUUID) {
    return typeof randomUUID === "function" ? randomUUID() : fallbackId();
  }

  function createLeaseClient(deps) {
    var fetchImpl = deps.fetchImpl;
    var setTimer = deps.setTimeoutImpl || setTimeout;
    var clearTimer = deps.clearTimeoutImpl || clearTimeout;
    var visibilityTarget = deps.visibilityTarget || null;
    var pageTarget = deps.pageTarget || null;
    var getVisibility = deps.getVisibility || function () {
      return visibilityTarget ? visibilityTarget.visibilityState : "visible";
    };
    var randomUUID = deps.randomUUID;
    var controllerId = makeControllerId(randomUUID);
    var authorization = null;
    var timer = null;
    var active = false;
    var disposed = false;
    var stateHandler = deps.onState || function () {};
    var heartbeatSeconds = DEFAULT_HEARTBEAT_SECONDS;
    var ttlSeconds = DEFAULT_TTL_SECONDS;
    var missedHeartbeats = 0;
    // Set by release() and consumed by pageshow: a restored bfcache page still
    // holds its credential in memory, so it can simply claim again instead of
    // sitting on a "Control released" badge with no visible way back.
    var releasedForTeardown = false;

    function emit(state, detail) {
      stateHandler(state, detail || null);
    }

    function stopHeartbeat() {
      active = false;
      if (timer !== null) {
        clearTimer(timer);
        timer = null;
      }
    }

    function scheduleHeartbeat() {
      if (!active || disposed) {
        return;
      }
      if (timer !== null) {
        clearTimer(timer);
      }
      timer = setTimer(function () {
        timer = null;
        // Returned so a test or host awaiting the timer observes the full
        // renewal chain, including the next scheduleHeartbeat.
        return heartbeat();
      }, heartbeatSeconds * 1000);
    }

    function errorFrom(response, payload) {
      var error = new Error("controller lease failed with status " + response.status);
      error.status = response.status;
      error.detail = payload && payload.detail ? payload.detail : null;
      return error;
    }

    function request(name, keepalive) {
      if (!authorization) {
        return Promise.resolve(null);
      }
      return fetchImpl(deps.urls[name], {
        method: "POST",
        headers: {
          Accept: "application/json",
          Authorization: "Bearer " + authorization,
          [CONTROLLER_HEADER]: controllerId,
        },
        keepalive: !!keepalive,
      }).then(function (response) {
        return response
          .json()
          .catch(function () {
            return null;
          })
          .then(function (payload) {
            if (!response.ok) {
              throw errorFrom(response, payload);
            }
            // A body that failed .json() arrives as null; fall back to the
            // defaults rather than dereferencing it.
            ttlSeconds = (payload && payload.lease_ttl_seconds) || DEFAULT_TTL_SECONDS;
            heartbeatSeconds = (payload && payload.heartbeat_interval_seconds) || DEFAULT_HEARTBEAT_SECONDS;
            return payload;
          });
      });
    }

    function becomeActive(payload) {
      active = true;
      missedHeartbeats = 0;
      releasedForTeardown = false;
      emit("active", payload);
      scheduleHeartbeat();
      return payload;
    }

    function retryDelaySeconds() {
      return Math.max(1, Math.floor(ttlSeconds / 3));
    }

    function heartbeatFailed(error) {
      stopHeartbeat();
      if (error && error.status === 409) {
        // Authoritative: the server stated ownership moved or expired. No
        // retry can help; surface lease-lost immediately.
        emit("lease-lost", error);
        return null;
      }
      missedHeartbeats += 1;
      if (missedHeartbeats <= MAX_MISSED_HEARTBEATS) {
        // Fail closed while retrying (active is false, so commands are
        // gated), but stay inside the lease's TTL window.
        emit("pending", error);
        timer = setTimer(function () {
          timer = null;
          return sendHeartbeat();
        }, retryDelaySeconds() * 1000);
        return null;
      }
      emit("unavailable", error);
      return null;
    }

    function fail(error, conflictState) {
      stopHeartbeat();
      if (error && error.status === 409) {
        emit(conflictState, error);
      } else {
        emit("unavailable", error);
      }
      return null;
    }

    function claim(secret) {
      if (secret !== undefined) {
        authorization = secret;
      }
      stopHeartbeat();
      return request("claim").then(becomeActive, function (error) {
        return fail(error, "read-only");
      });
    }

    function heartbeat() {
      if (!active || disposed) {
        return Promise.resolve(null);
      }
      return sendHeartbeat();
    }

    // The raw renewal round trip, callable while inactive: the miss-retry
    // timer runs after a blip already stopped the heartbeat, so it cannot go
    // through heartbeat()'s active guard.
    function sendHeartbeat() {
      return request("heartbeat").then(becomeActive, heartbeatFailed);
    }

    function takeover() {
      stopHeartbeat();
      return request("takeover").then(becomeActive, function (error) {
        return fail(error, error && error.status === 409 ? "read-only" : "unavailable");
      });
    }

    function release(keepalive) {
      stopHeartbeat();
      releasedForTeardown = !!keepalive;
      return request("release", keepalive).then(
        function (payload) {
          emit("released", payload);
          return payload;
        },
        function () {
          return null;
        },
      );
    }

    function renewIfVisible() {
      if (active && getVisibility() !== "hidden") {
        heartbeat();
        return;
      }
      // A bfcache restore (or a throttled-tab return) of a panel that released
      // on pagehide: the credential is still in the closure, so re-claim.
      // Claim is an idempotent compare-and-set on the server — if another
      // controller took the scope meanwhile it answers 409 and this panel
      // lands in read-only rather than stealing anything.
      if (!active && releasedForTeardown && authorization && getVisibility() !== "hidden") {
        claim();
      }
    }

    function onVisibilityChange() {
      renewIfVisible();
    }

    function onPageShow() {
      renewIfVisible();
    }

    function onPageHide() {
      release(true);
    }

    if (visibilityTarget && visibilityTarget.addEventListener) {
      visibilityTarget.addEventListener("visibilitychange", onVisibilityChange);
    }
    if (pageTarget && pageTarget.addEventListener) {
      pageTarget.addEventListener("pageshow", onPageShow);
      pageTarget.addEventListener("pagehide", onPageHide);
    }

    return {
      claim: claim,
      heartbeat: heartbeat,
      takeover: takeover,
      release: release,
      controllerHeader: function () {
        return active ? controllerId : null;
      },
      isActive: function () {
        return active;
      },
      timings: function () {
        return { leaseTtlSeconds: ttlSeconds, heartbeatIntervalSeconds: heartbeatSeconds };
      },
      markLost: function (error) {
        stopHeartbeat();
        emit("lease-lost", error || null);
      },
      setStateHandler: function (handler) {
        stateHandler = handler;
      },
      dispose: function () {
        disposed = true;
        stopHeartbeat();
        if (visibilityTarget && visibilityTarget.removeEventListener) {
          visibilityTarget.removeEventListener("visibilitychange", onVisibilityChange);
        }
        if (pageTarget && pageTarget.removeEventListener) {
          pageTarget.removeEventListener("pageshow", onPageShow);
          pageTarget.removeEventListener("pagehide", onPageHide);
        }
      },
    };
  }

  return {
    CONTROLLER_HEADER: CONTROLLER_HEADER,
    DEFAULT_TTL_SECONDS: DEFAULT_TTL_SECONDS,
    DEFAULT_HEARTBEAT_SECONDS: DEFAULT_HEARTBEAT_SECONDS,
    createLeaseClient: createLeaseClient,
  };
});
