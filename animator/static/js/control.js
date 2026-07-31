//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// The reveal operator panel.
//
// Two properties dominate this file:
//
//   1. **The secret lives in one closure variable and nowhere else.** It is read
//      from the password field, the field is cleared immediately, and from then
//      on it exists only as an Authorization header. It is never written to a
//      URL, to localStorage/sessionStorage, to a cookie, to the DOM, or to a log
//      — so it cannot leak through an access log, a Referer, browser history, or
//      a shared machine. Reloading the page requires typing it again, which is
//      the intended cost.
//
//   2. **An unknown outcome must never let the ceremony advance twice.** A
//      refusal the server *stated* (400/403/404/409/422) changed nothing, so the
//      controls re-enable at once. But a network failure or any 5xx — including
//      the 503 the store raises, which may arrive after a fenced save already
//      committed — is ambiguous. There the controls stay disabled until the
//      operator explicitly reloads authoritative state. Re-enabling
//      optimistically would let an operator press "step" again and silently
//      reveal two teams in front of an audience.
//
// The command client is separated from the DOM glue and takes its dependencies
// explicitly, so the contract above is exercised by a Node test with no browser.
// Exported as UMD: `window.AnimatorControl` / `module.exports`.
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.AnimatorControl = api;
  }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  // The team-naming rule is shared with the projector, so the operator and the
  // audience never refer to a team differently.
  var format =
    typeof module !== "undefined" && module.exports
      ? require("./cell-format.js")
      : (typeof window !== "undefined" ? window : {}).AnimatorCellFormat;

  var UNKNOWN_OUTCOME =
    "Command outcome unknown — controls are locked until the ceremony state is reloaded.";
  var RELOAD_REQUIRED =
    "Command delivery was not confirmed. It may or may not have been applied. " +
    "Restore the connection, then use Reload state.";
  var RECONCILE_FAILED =
    "The ceremony state could not be reloaded. Reload it before issuing another command.";
  var RELOADING_STATE = "Reloading authoritative ceremony state…";
  var UNUSABLE_STATE_DETAIL = "The stored reveal session is unusable.";
  var DEFINITIVE_STATUSES = [400, 403, 404, 409, 422];

  // A failure the server described is definitive: nothing was applied.
  // Everything else (no status at all, or 5xx) is ambiguous.
  function isDefinitive(status) {
    return typeof status === "number" && DEFINITIVE_STATUSES.indexOf(status) !== -1;
  }

  // One key per command attempt, sent as `Idempotency-Key`. It names *this*
  // attempt so that a retry of it — by us, by a proxy, or by an operator — is
  // recognized by the server and replayed instead of applied a second time. It
  // is not a credential: it grants nothing and is deliberately not derived from
  // the secret. Must satisfy the server's ^[A-Za-z0-9_-]{8,128}$, which a v4
  // UUID does; the fallback exists for browsers without `crypto.randomUUID`
  // (and for a non-secure context, where it is undefined even in modern ones).
  var keyCounter = 0;
  function makeIdempotencyKey() {
    var uuid = typeof crypto !== "undefined" && crypto && typeof crypto.randomUUID === "function";
    if (uuid) {
      return crypto.randomUUID();
    }
    keyCounter += 1;
    return "cmd-" + Date.now().toString(36) + "-" + keyCounter + "-" + Math.random().toString(36).slice(2, 12);
  }

  // Which controls the current projection makes meaningful. This is the single
  // source of truth for the state-driven panel; renderState and the keyboard
  // handler both consume it.
  //
  // Back is intentionally NOT gated on revealed_count: a step can be a pure
  // cursor move with no reveal, so "0 revealed" does not mean "nothing to
  // undo", and the projection exposes no step-log length to compute it with.
  // back() on an empty trail is a harmless no-op, so it stays visible whenever
  // a session is past idle.
  function controlsForState(projection, stateUnusable, stateLoadFailed) {
    if (stateUnusable) {
      return {
        startVisible: false,
        startOverVisible: false,
        resetVisible: false,
        stepVisible: false,
        backVisible: false,
        jumpVisible: false,
      };
    }
    if (stateLoadFailed) {
      return {
        startVisible: false,
        startOverVisible: false,
        resetVisible: false,
        stepVisible: false,
        backVisible: false,
        jumpVisible: false,
      };
    }
    if (!projection || projection.phase === "idle") {
      return {
        startVisible: true,
        startOverVisible: false,
        resetVisible: false,
        stepVisible: false,
        backVisible: false,
        jumpVisible: false,
      };
    }
    var revealing = projection.phase === "revealing";
    return {
      startVisible: false,
      startOverVisible: true,
      resetVisible: true,
      stepVisible: revealing,
      backVisible: true,
      jumpVisible: revealing,
    };
  }

  // deps: { fetchImpl, urls, onState, onStatus, onError, setBusy, onAuthFailure,
  //         onReconcileFailure }
  function createCommandClient(deps) {
    var secret = null;
    var busy = false;
    var blocked = false; // Set after an ambiguous outcome until reconciliation.
    var sequenceActive = false;

    function headers(withBody, idempotencyKey) {
      var result = { Accept: "application/json", Authorization: "Bearer " + secret };
      if (withBody) {
        result["Content-Type"] = "application/json";
      }
      if (idempotencyKey) {
        result["Idempotency-Key"] = idempotencyKey;
      }
      return result;
    }

    function request(url, options) {
      var opts = options || {};
      var init = { method: opts.method || "GET", headers: headers(!!opts.body, opts.idempotencyKey) };
      if (opts.body) {
        init.body = JSON.stringify(opts.body);
      }
      return deps.fetchImpl(url, init).then(function (response) {
        if (response.ok) {
          return response.json();
        }
        return response
          .json()
          .catch(function () {
            return null;
          })
          .then(function (payload) {
            var error = new Error("command failed with status " + response.status);
            error.status = response.status;
            error.detail = payload && payload.detail ? payload.detail : null;
            throw error;
          });
      });
    }

    // Every authentication failure is handled in one place, so that unlocking
    // with a wrong secret behaves exactly like a command rejected with 403:
    // the credential is forgotten and the operator is re-prompted. Handling it
    // only in run() would leave an invalid secret sitting in memory behind a
    // visible command panel after a failed unlock.
    function handleForbidden() {
      secret = null;
      blocked = false;
      deps.setBusy(false);
      deps.onAuthFailure();
    }

    // Load authoritative state. A 404 is not an error here: it is the honest
    // "this credential has no ceremony yet" answer.
    function loadState() {
      return request(deps.urls.state).then(
        function (projection) {
          deps.onState(projection);
          return projection;
        },
        function (error) {
          if (error.status === 404) {
            deps.onState(null);
            return null;
          }
          if (error.status === 403) {
            handleForbidden();
            return null;
          }
          throw error;
        },
      );
    }

    function reconcile() {
      if (busy || secret === null) {
        return Promise.resolve(null);
      }
      busy = true;
      deps.setBusy(true);
      deps.onStatus(RELOADING_STATE);
      return loadState().then(
        function (projection) {
          busy = false;
          blocked = false;
          if (!sequenceActive) {
            deps.setBusy(false);
          }
          deps.onStatus("");
          return projection;
        },
        function (error) {
          // Still ambiguous: keep the controls locked rather than risk a double
          // step, and offer an explicit reload instead.
          busy = false;
          blocked = true;
          deps.onReconcileFailure(RECONCILE_FAILED, error);
          return null;
        },
      );
    }

    function run(url, options) {
      if (busy || blocked || secret === null) {
        return Promise.resolve(null);
      }
      busy = true;
      deps.setBusy(true);
      // A fresh key per attempt: two deliberate presses of "step" are two
      // commands and must both apply. Only a *repeat of one attempt* — which
      // this panel never issues by itself — reuses a key.
      var opts = options || {};
      opts.idempotencyKey = makeIdempotencyKey();
      return request(url, opts).then(
        function (projection) {
          busy = false;
          blocked = false;
          if (!sequenceActive) {
            deps.setBusy(false);
          }
          deps.onState(projection);
          return projection;
        },
        function (error) {
          busy = false;
          if (error.status === 403) {
            // The credential is invalid or expired: forget it and re-prompt.
            handleForbidden();
            return null;
          }
          if (isDefinitive(error.status)) {
            blocked = false;
            if (!sequenceActive) {
              deps.setBusy(false);
            }
            deps.onError(error.detail || "The command was refused.", error);
            return null;
          }
          // Ambiguous: controls stay disabled until explicit reconciliation.
          blocked = true;
          deps.onStatus(UNKNOWN_OUTCOME);
          deps.onReconcileFailure(RELOAD_REQUIRED, error);
          // Do not auto-reload here. Apart from making the warning disappear too
          // quickly, a cached or immediately recovered GET would unlock the
          // panel without requiring the operator to acknowledge the unknown
          // command outcome.
          return null;
        },
      );
    }

    // Send one existing command at a time. The sequence stops on the first
    // response that is not a confirmed success; no bulk route or bus command is
    // introduced, and an ambiguous result can never consume an extra step.
    function repeat(url, count, statusLabel) {
      var total = typeof count === "number" && isFinite(count) ? Math.max(0, Math.floor(count)) : 0;
      if (total === 0 || busy || blocked || secret === null) {
        return Promise.resolve(null);
      }
      var completed = 0;
      var lastProjection = null;
      sequenceActive = true;
      deps.setBusy(true);
      deps.onStatus(statusLabel + " 0 / " + total + "…");

      function runNext() {
        if (completed === total) {
          return Promise.resolve(lastProjection);
        }
        return run(url, { method: "POST" }).then(function (projection) {
          if (projection === null) {
            return null;
          }
          completed += 1;
          lastProjection = projection;
          deps.onStatus(statusLabel + " " + completed + " / " + total + "…");
          return runNext();
        });
      }

      return runNext().then(function (projection) {
        sequenceActive = false;
        if (!blocked && secret !== null) {
          deps.setBusy(false);
          deps.onStatus("");
        }
        return projection;
      });
    }

    return {
      setSecret: function (value) {
        secret = value;
        blocked = false;
      },
      hasSecret: function () {
        return secret !== null;
      },
      isBlocked: function () {
        return blocked;
      },
      loadState: loadState,
      reload: function () {
        return reconcile();
      },
      start: function (siteId, restart) {
        return run(deps.urls.start, {
          method: "POST",
          body: { site_id: siteId === "" ? null : siteId, restart: !!restart },
        });
      },
      step: function () {
        return run(deps.urls.step, { method: "POST" });
      },
      stepMany: function (count) {
        return repeat(deps.urls.step, count, "Advancing");
      },
      back: function () {
        return run(deps.urls.back, { method: "POST" });
      },
      backMany: function (count) {
        return repeat(deps.urls.back, count, "Returning");
      },
      reset: function () {
        return run(deps.urls.reset, { method: "POST" });
      },
      jump: function (teamId) {
        return run(deps.urls.jump, { method: "POST", body: { team_id: teamId } });
      },
    };
  }

  // Shortcuts must never fire while the operator is typing — including in the
  // secret field, where a stray arrow key would otherwise step the ceremony.
  function isFormControl(target) {
    if (!target || !target.tagName) {
      return false;
    }
    if (target.isContentEditable) {
      return true;
    }
    return ["INPUT", "SELECT", "TEXTAREA", "BUTTON", "OPTION"].indexOf(target.tagName.toUpperCase()) !== -1;
  }

  function isUnusableStateError(error) {
    return !!error && error.status === 500 && error.detail === UNUSABLE_STATE_DETAIL;
  }

  function boot(doc) {
    var root = doc.getElementById("control-app");
    if (!root) {
      return null;
    }

    var els = {
      form: doc.getElementById("control-secret-form"),
      secret: doc.getElementById("control-secret"),
      panel: doc.getElementById("control-panel"),
      status: doc.getElementById("control-status"),
      error: doc.getElementById("control-error"),
      scope: doc.getElementById("control-scope"),
      scopeLabel: doc.getElementById("control-scope-label"),
      jumpRow: doc.getElementById("control-jump-row"),
      jumpTeam: doc.getElementById("control-jump-team"),
      confirmationModal: doc.getElementById("control-confirmation-modal"),
      confirmationTitle: doc.getElementById("control-confirmation-modal-label"),
      confirmationMessage: doc.getElementById("control-confirmation-message"),
      confirmationCounts: doc.getElementById("control-confirmation-counts"),
      phase: doc.getElementById("control-state-phase"),
      scopeOut: doc.getElementById("control-state-scope"),
      revealed: doc.getElementById("control-state-revealed"),
      focus: doc.getElementById("control-state-focus"),
    };
    var buttons = {
      start: doc.getElementById("control-start"),
      startOver: doc.getElementById("control-start-over"),
      rebuild: doc.getElementById("control-rebuild"),
      reset: doc.getElementById("control-reset"),
      confirmationConfirm: doc.getElementById("control-confirmation-confirm"),
      reload: doc.getElementById("control-reload"),
      step: doc.getElementById("control-step"),
      stepTen: doc.getElementById("control-step-ten"),
      back: doc.getElementById("control-back"),
      backTen: doc.getElementById("control-back-ten"),
      jump: doc.getElementById("control-jump"),
    };
    // The last projection the server confirmed; feeds the visibility mapping,
    // the start-over modal counts, and the keyboard gating.
    var lastProjection = null;
    var stateUnusable = false;
    var stateLoadFailed = false;
    var pendingDestructiveAction = null;
    var confirmationModal = window.bootstrap.Modal.getOrCreateInstance(els.confirmationModal);

    function setStatus(text) {
      if (els.status) {
        els.status.textContent = text;
      }
    }

    function setError(text) {
      if (!els.error) {
        return;
      }
      els.error.textContent = text || "";
      els.error.hidden = !text;
    }

    function setBusy(isBusy) {
      Object.keys(buttons).forEach(function (name) {
        if (buttons[name]) {
          buttons[name].disabled = isBusy;
        }
      });
    }

    function renderState(projection) {
      var controls = controlsForState(projection, stateUnusable, stateLoadFailed);
      buttons.start.hidden = !controls.startVisible;
      buttons.startOver.hidden = !controls.startOverVisible;
      buttons.reset.hidden = !controls.resetVisible;
      buttons.step.hidden = !controls.stepVisible;
      buttons.stepTen.hidden = !controls.stepVisible;
      buttons.back.hidden = !controls.backVisible;
      buttons.backTen.hidden = !controls.backVisible;
      els.jumpRow.hidden = !controls.jumpVisible;
      // Once a session exists the projection names the token's scope, so the
      // selector has nothing left to declare — collapse it to static text. A
      // fresh ceremony (no projection) keeps the selector: it is the only way
      // to tell start-reveal which scope the token authorizes.
      els.scope.hidden = projection !== null;
      els.scopeLabel.hidden = projection === null;
      if (projection) {
        els.scopeLabel.textContent = projection.site_name || "Global ceremony";
      }
      if (!projection) {
        els.phase.textContent = "no ceremony";
        els.scopeOut.textContent = "—";
        els.revealed.textContent = "—";
        els.focus.textContent = "—";
        els.jumpTeam.disabled = true;
        buttons.jump.disabled = true;
        return;
      }
      els.phase.textContent = projection.phase;
      els.scopeOut.textContent = projection.site_name || "Global";
      els.revealed.textContent = projection.revealed_count + " / " + projection.frozen_count;
      var teams = projection.teams || [];
      var focused = null;
      while (els.jumpTeam.firstChild) {
        els.jumpTeam.removeChild(els.jumpTeam.firstChild);
      }
      teams.forEach(function (team) {
        var option = doc.createElement("option");
        option.setAttribute("value", team.team_id);
        option.textContent = team.current_rank + ". " + format.teamLabel(team);
        els.jumpTeam.appendChild(option);
        if (team.team_id === projection.focused_team_id) {
          focused = team;
        }
      });
      els.focus.textContent = focused ? format.teamLabel(focused) : "—";
      els.jumpTeam.disabled = teams.length === 0;
      buttons.jump.disabled = teams.length === 0;
    }

    var client = createCommandClient({
      fetchImpl: function (url, init) {
        return window.fetch(url, init);
      },
      urls: {
        state: root.getAttribute("data-state-url"),
        start: root.getAttribute("data-start-url"),
        step: root.getAttribute("data-step-url"),
        back: root.getAttribute("data-back-url"),
        reset: root.getAttribute("data-reset-url"),
        jump: root.getAttribute("data-jump-url"),
      },
      onState: function (projection) {
        lastProjection = projection;
        stateUnusable = false;
        stateLoadFailed = false;
        setError("");
        renderState(projection);
      },
      onStatus: setStatus,
      onError: function (message) {
        setError(message);
      },
      setBusy: setBusy,
      onAuthFailure: function () {
        lastProjection = null;
        stateUnusable = false;
        stateLoadFailed = false;
        els.panel.hidden = true;
        els.form.hidden = false;
        setError("Invalid or expired operator secret. Enter it again.");
        setStatus("Enter the operator secret to begin.");
      },
      onReconcileFailure: function (message) {
        setError(message);
      },
    });

    // The public /meta feed supplies the site ids a start-reveal body must carry,
    // so the operator picks from server-provided values rather than typing one.
    function loadScopes() {
      var initialScope = root.getAttribute("data-initial-scope") || "global";
      return window
        .fetch(root.getAttribute("data-meta-url"), { headers: { Accept: "application/json" } })
        .then(function (response) {
          return response.ok ? response.json() : null;
        })
        .then(function (meta) {
          if (!meta || !meta.sites) {
            return;
          }
          meta.sites.forEach(function (site) {
            var option = doc.createElement("option");
            option.setAttribute("value", site.site_id);
            option.textContent = site.name;
            els.scope.appendChild(option);
          });
          els.scope.value = initialScope === "global" ? "" : initialScope;
        })
        .catch(function () {
          // A missing site list only limits the scope selector to "global"; the
          // panel still works for a global ceremony.
        });
    }

    els.form.addEventListener("submit", function (event) {
      event.preventDefault();
      var value = els.secret.value;
      if (!value) {
        return;
      }
      client.setSecret(value);
      lastProjection = null;
      stateUnusable = false;
      stateLoadFailed = false;
      // Out of the DOM immediately: it lives in the closure from here on.
      els.secret.value = "";
      els.form.hidden = true;
      els.panel.hidden = false;
      setError("");
      setStatus("");
      client.loadState().catch(function (error) {
        stateUnusable = isUnusableStateError(error);
        stateLoadFailed = !stateUnusable;
        renderState(null);
        setError(
          stateUnusable
            ? "The stored reveal state cannot be read. Use Rebuild state to replace it."
            : "The ceremony state could not be loaded.",
        );
      });
    });

    // The scope a start body must declare: the projection's own site once a
    // session exists (the selector is hidden then, and its value may be stale),
    // otherwise the selector's preselected value.
    function currentSiteId() {
      if (lastProjection) {
        return lastProjection.site_id === null ? "" : lastProjection.site_id;
      }
      return els.scope.value;
    }

    buttons.start.addEventListener("click", function () {
      client.start(currentSiteId(), false);
    });

    function showConfirmation(action) {
      pendingDestructiveAction = action;
      els.confirmationCounts.hidden = true;
      if (action === "start-over") {
        els.confirmationTitle.textContent = "Start the ceremony over?";
        els.confirmationMessage.textContent =
          "This rebuilds the ranking from current contest data. Runs judged since the ceremony began are included, " +
          "so the reveal order may differ from what was already shown.";
        els.confirmationCounts.textContent = lastProjection
          ? lastProjection.revealed_count + " / " + lastProjection.frozen_count + " revealed"
          : "";
        els.confirmationCounts.hidden = false;
        buttons.confirmationConfirm.textContent = "Discard and start over";
      } else if (action === "rebuild") {
        els.confirmationTitle.textContent = "Rebuild the reveal state?";
        els.confirmationMessage.textContent =
          "The stored state cannot be read. Rebuilding replaces it with a new frozen snapshot from current contest " +
          "data and starts the ceremony.";
        buttons.confirmationConfirm.textContent = "Rebuild state";
      } else {
        els.confirmationTitle.textContent = "Reset the ceremony to idle?";
        els.confirmationMessage.textContent =
          "This clears the reveal progress but preserves the original frozen contest snapshot. Start reveal must be " +
          "selected again before the ceremony can continue.";
        buttons.confirmationConfirm.textContent = "Reset to idle";
      }
      confirmationModal.show();
    }

    buttons.startOver.addEventListener("click", function () {
      showConfirmation("start-over");
    });
    buttons.rebuild.addEventListener("click", function () {
      showConfirmation("rebuild");
    });
    buttons.reset.addEventListener("click", function () {
      showConfirmation("reset");
    });
    buttons.confirmationConfirm.addEventListener("click", function () {
      var action = pendingDestructiveAction;
      pendingDestructiveAction = null;
      confirmationModal.hide();
      if (action === "start-over" || action === "rebuild") {
        client.start(currentSiteId(), true);
      } else if (action === "reset") {
        client.reset();
      }
    });
    buttons.step.addEventListener("click", function () {
      client.step();
    });
    buttons.stepTen.addEventListener("click", function () {
      client.stepMany(10);
    });
    buttons.back.addEventListener("click", function () {
      client.back();
    });
    buttons.backTen.addEventListener("click", function () {
      client.backMany(10);
    });
    buttons.jump.addEventListener("click", function () {
      if (els.jumpTeam.value) {
        client.jump(els.jumpTeam.value);
      }
    });
    buttons.reload.addEventListener("click", function () {
      client.reload();
    });

    doc.addEventListener("keydown", function (event) {
      if (isFormControl(event.target) || !client.hasSecret()) {
        return;
      }
      var controls = controlsForState(lastProjection, stateUnusable, stateLoadFailed);
      if (event.key === "ArrowRight" && controls.stepVisible) {
        client.step();
      } else if (event.key === "ArrowLeft" && controls.backVisible) {
        client.back();
      }
    });

    loadScopes();
    return client;
  }

  if (typeof document !== "undefined" && typeof window !== "undefined" && window.fetch) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () {
        boot(document);
      });
    } else {
      boot(document);
    }
  }

  return {
    UNKNOWN_OUTCOME: UNKNOWN_OUTCOME,
    RELOAD_REQUIRED: RELOAD_REQUIRED,
    RECONCILE_FAILED: RECONCILE_FAILED,
    RELOADING_STATE: RELOADING_STATE,
    UNUSABLE_STATE_DETAIL: UNUSABLE_STATE_DETAIL,
    createCommandClient: createCommandClient,
    controlsForState: controlsForState,
    isDefinitive: isDefinitive,
    isFormControl: isFormControl,
    isUnusableStateError: isUnusableStateError,
    boot: boot,
  };
});
