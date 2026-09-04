//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Controller ownership state and operator-panel UI glue. Exported as UMD:
// `window.AnimatorControlOwnership` / `module.exports`.
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.AnimatorControlOwnership = api;
  }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  var COPY = {
    pending: {
      label: "Checking control",
      detail: "Verifying command authority for this panel.",
      badge: "text-bg-secondary",
    },
    active: {
      label: "In control",
      detail: "This panel can issue ceremony commands.",
      badge: "text-bg-success",
    },
    "read-only": {
      label: "Read-only",
      detail: "Another panel controls this ceremony. The projection remains available here.",
      badge: "text-bg-warning",
    },
    "lease-lost": {
      label: "Control lost",
      detail: "This panel no longer has command authority. No further commands will be sent.",
      badge: "text-bg-danger",
    },
    unavailable: {
      label: "Control unavailable",
      detail: "Ownership could not be verified. Commands are disabled until you retry successfully.",
      badge: "text-bg-danger",
    },
    released: {
      label: "Control released",
      detail: "This panel is no longer issuing ceremony commands.",
      badge: "text-bg-secondary",
    },
  };

  // The projector readout: how many `/reveal/events` streams the server counts
  // in this scope, reported on every successful lease operation. `undefined`
  // means the server has not reported (nothing is rendered); `null` means it
  // could not tell, which is shown as such -- an outage must never look like an
  // empty hall.
  function projectorCopy(count) {
    if (count === null) {
      return { text: "Projector count unavailable", modifier: "is-unknown" };
    }
    if (count === 0) {
      return { text: "No projectors connected", modifier: "is-empty" };
    }
    return { text: count + (count === 1 ? " projector" : " projectors") + " connected", modifier: "" };
  }

  function createOwnershipController(deps) {
    var state = "pending";
    var takeoverPending = false;
    var retryPending = false;
    var projectorCount;

    function renderProjectors() {
      var el = deps.elements && deps.elements.projectors;
      if (!el) {
        return;
      }
      if (state !== "active" || projectorCount === undefined) {
        el.hidden = true;
        el.textContent = "";
        el.className = "control-projectors";
        return;
      }
      var copy = projectorCopy(projectorCount);
      el.textContent = copy.text;
      el.className = "control-projectors" + (copy.modifier ? " " + copy.modifier : "");
      el.hidden = false;
    }

    function render(nextState) {
      state = nextState;
      var copy = COPY[state] || COPY.unavailable;
      if (deps.elements) {
        deps.elements.label.textContent = copy.label;
        deps.elements.label.className = "badge " + copy.badge;
        deps.elements.detail.textContent = copy.detail;
        deps.elements.takeover.hidden = state !== "read-only" && state !== "lease-lost";
        deps.elements.retry.hidden = state !== "unavailable";
        deps.elements.takeover.disabled = takeoverPending;
        deps.elements.retry.disabled = retryPending;
      }
      renderProjectors();
      deps.setCommandsEnabled(state === "active");
      if (deps.onState) {
        deps.onState(state);
      }
    }

    function handleLeaseState(nextState, detail) {
      takeoverPending = false;
      retryPending = false;
      // Only a lease payload carries the count; an error detail never does, and
      // a state without one (pending, released) forgets the last reading rather
      // than showing a stale number under a badge that no longer means control.
      if (nextState === "active" && detail && Object.prototype.hasOwnProperty.call(detail, "projector_count")) {
        projectorCount = detail.projector_count;
      } else if (nextState !== "active") {
        projectorCount = undefined;
      }
      render(nextState);
    }

    deps.lease.setStateHandler(handleLeaseState);

    function claim(secret) {
      render("pending");
      return deps.lease.claim(secret);
    }

    function retry() {
      if (state !== "unavailable" || retryPending) {
        return Promise.resolve(null);
      }
      retryPending = true;
      render("pending");
      return deps.lease.claim();
    }

    function confirmTakeover() {
      if ((state !== "read-only" && state !== "lease-lost") || takeoverPending) {
        return;
      }
      deps.confirmTakeover(function () {
        takeoverPending = true;
        render("pending");
        deps.lease.takeover();
      });
    }

    if (deps.elements) {
      deps.elements.takeover.addEventListener("click", confirmTakeover);
      deps.elements.retry.addEventListener("click", retry);
    }
    render("pending");

    return {
      claim: claim,
      retry: retry,
      requestTakeover: confirmTakeover,
      handleLeaseState: handleLeaseState,
      canCommand: function () {
        return state === "active" && deps.lease.isActive();
      },
      controllerHeader: deps.lease.controllerHeader,
      markLost: deps.lease.markLost,
      state: function () {
        return state;
      },
      projectorCount: function () {
        return projectorCount;
      },
    };
  }

  return {
    COPY: COPY,
    projectorCopy: projectorCopy,
    createOwnershipController: createOwnershipController,
  };
});
