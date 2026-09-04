//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Connection-status presentation for the Animator scoreboard. Keeps the
// outage clock independent from transport retries so Reconnecting -> Polling
// remains one continuous duration. Exported as UMD for browser and Node tests.
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.AnimatorConnectionStatus = api;
  }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  var DEGRADED_STATUSES = {
    polling: true,
    reconnecting: true,
  };
  var LABELS = {
    live: "Live",
    polling: "Polling",
    reconnecting: "Reconnecting…",
    // An ended contest whose board is still frozen. It is not "Live": nothing is
    // streaming, because nothing is published for it to stream. The board is
    // re-reading the feeds on a timer waiting for the scoreboard to be released,
    // and the label says that rather than telling a room the contest is running.
    waiting: "Waiting for results",
  };

  function formatElapsed(elapsedMs) {
    var totalSeconds = Math.max(0, Math.floor(elapsedMs / 1000));
    var minutes = Math.floor(totalSeconds / 60);
    var seconds = totalSeconds % 60;
    return String(minutes).padStart(2, "0") + ":" + String(seconds).padStart(2, "0");
  }

  function setHidden(element, hidden) {
    if (!element) {
      return;
    }
    if (hidden) {
      element.setAttribute("hidden", "");
    } else {
      element.removeAttribute("hidden");
    }
  }

  function createConnectionStatus(options) {
    var startedAt = null;
    var intervalHandle = null;

    function renderElapsed() {
      if (startedAt === null || !options.timer) {
        return;
      }
      var text = formatElapsed(options.now() - startedAt);
      options.timer.textContent = text;
      options.timer.setAttribute("aria-label", "Connection disruption duration: " + text);
    }

    function stopTimer() {
      if (intervalHandle !== null) {
        options.clearInterval(intervalHandle);
        intervalHandle = null;
      }
      startedAt = null;
      setHidden(options.timer, true);
    }

    function startTimer() {
      if (startedAt === null) {
        startedAt = options.now();
      }
      renderElapsed();
      setHidden(options.timer, false);
      if (intervalHandle === null) {
        intervalHandle = options.setInterval(renderElapsed, 1000);
      }
    }

    function setStatus(status) {
      if (options.container) {
        options.container.setAttribute("data-status", status);
      }
      if (options.label) {
        options.label.textContent = LABELS[status] || "";
      }
      if (DEGRADED_STATUSES[status]) {
        startTimer();
      } else {
        stopTimer();
      }
    }

    return {
      setStatus: setStatus,
      _state: function () {
        return { startedAt: startedAt, intervalHandle: intervalHandle };
      },
    };
  }

  return {
    formatElapsed: formatElapsed,
    createConnectionStatus: createConnectionStatus,
  };
});
