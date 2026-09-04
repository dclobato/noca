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

  // "Did the ceremony move" is shared with the projector for the same reason:
  // the projector closes its media overlay on that signal and this panel resets
  // its Show/Hide label on it, so two definitions would drift into a button
  // describing the opposite of what is on screen. Only the pure signature is
  // used here — the controller half of that module drives a dialog this page
  // does not have.
  var cueApi =
    typeof module !== "undefined" && module.exports
      ? require("./ceremony-media-cue.js")
      : (typeof window !== "undefined" ? window : {}).CeremonyMediaCue;

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
  // The one authored `409` that means controller ownership specifically ended.
  // Must match animator/routes/controller_lease.py verbatim; the Android client
  // pins the same string.
  var LEASE_LOST_DETAIL = "This controller no longer owns the ceremony.";

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
        jumpPendingVisible: false,
        mediaVisible: false,
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
        jumpPendingVisible: false,
        mediaVisible: false,
      };
    }
    if (!projection) {
      // No stored ceremony at all: there is nothing to rebuild, so plain Start
      // is the only meaningful command.
      return {
        startVisible: true,
        startOverVisible: false,
        resetVisible: false,
        stepVisible: false,
        backVisible: false,
        jumpVisible: false,
        jumpPendingVisible: false,
        mediaVisible: false,
      };
    }
    if (projection.phase === "idle") {
      // A *stored* idle ceremony. Plain Start reuses that stored session — and
      // with it the medal cutoffs snapshotted when it was created. Start over is
      // the only way to rebuild from current settings, so it must be reachable
      // from here; without it a configuration change could never be adopted
      // through the UI.
      return {
        startVisible: true,
        startOverVisible: true,
        resetVisible: false,
        stepVisible: false,
        backVisible: false,
        jumpVisible: false,
        jumpPendingVisible: false,
        mediaVisible: false,
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
      jumpPendingVisible: revealing && !projection.next_cell,
      // Available while `done` as well as while `revealing`: the champion's
      // photo is the moment this control exists for, and by then the ceremony
      // has stopped stepping. It needs a focused team because the cue names no
      // team of its own — the server reads the ceremony's own cursor.
      mediaVisible: !!projection.focused_team_id,
    };
  }

  // deps: { fetchImpl, urls, ownership, onState, onStatus, onError, setBusy,
  //         onAuthFailure, onReconcileFailure }
  function createCommandClient(deps) {
    var secret = null;
    var busy = false;
    var blocked = false; // Set after an ambiguous outcome until reconciliation.
    var sequenceActive = false;

    function headers(withBody, idempotencyKey, controllerId) {
      var result = { Accept: "application/json", Authorization: "Bearer " + secret };
      if (withBody) {
        result["Content-Type"] = "application/json";
      }
      if (idempotencyKey) {
        result["Idempotency-Key"] = idempotencyKey;
      }
      if (controllerId) {
        result["X-Animator-Controller-Id"] = controllerId;
      }
      return result;
    }

    function request(url, options) {
      var opts = options || {};
      var init = {
        method: opts.method || "GET",
        headers: headers(!!opts.body, opts.idempotencyKey, opts.controllerId),
      };
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
      if (busy || blocked || secret === null || !deps.ownership.canCommand()) {
        return Promise.resolve(null);
      }
      busy = true;
      deps.setBusy(true);
      // A fresh key per attempt: two deliberate presses of "step" are two
      // commands and must both apply. Only a *repeat of one attempt* — which
      // this panel never issues by itself — reuses a key.
      var opts = options || {};
      opts.idempotencyKey = makeIdempotencyKey();
      opts.controllerId = deps.ownership.controllerHeader();
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
          if (error.status === 409 && error.detail === LEASE_LOST_DETAIL) {
            // A stated refusal: nothing was applied, so the ambiguity lock must
            // NOT engage. Without this, a panel that lost ownership and then
            // took over again would show "in control" while every command
            // silently no-ops on `blocked` until an explicit reload.
            blocked = false;
            if (!sequenceActive) {
              deps.setBusy(false);
            }
            deps.ownership.markLost(error);
            deps.onError(error.detail, error);
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
      if (total === 0 || busy || blocked || secret === null || !deps.ownership.canCommand()) {
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
      jumpPending: function () {
        return run(deps.urls.jumpPending, { method: "POST" });
      },
      // Raise or lower the focused team's media on every projector in scope.
      //
      // Deliberately NOT routed through run(), and the three differences are the
      // whole reason this command is safe in situations where a reveal command
      // is not:
      //
      //   * No Idempotency-Key. A cue persists nothing, so there is no receipt
      //     ring to match one against, and re-cueing is inherently a no-op.
      //   * A failure never sets `blocked`. The ambiguity lock exists to stop a
      //     retried `step` revealing two teams; a cue cannot reveal anything, so
      //     locking the pad over one would be pure cost.
      //   * `hide` is dispatched even while the pad IS blocked. That is the case
      //     it matters most in: an ambiguous 5xx with a photo covering the
      //     board is exactly when the operator needs the board back, and hiding
      //     can never double-apply.
      //
      // Answers 204, so there is no projection to apply and no state to update.
      // Resolves with true on success and false on any refusal.
      mediaCue: function (action) {
        var hiding = action === "hide";
        if (busy || secret === null || !deps.ownership.canCommand()) {
          return Promise.resolve(false);
        }
        if (blocked && !hiding) {
          return Promise.resolve(false);
        }
        var url = hiding ? deps.urls.hideMedia : deps.urls.showMedia;
        return deps
          .fetchImpl(url, {
            method: "POST",
            headers: headers(false, null, deps.ownership.controllerHeader()),
          })
          .then(
            function (response) {
              if (response.ok) {
                return true;
              }
              if (response.status === 403) {
                handleForbidden();
                return false;
              }
              return response
                .json()
                .catch(function () {
                  return null;
                })
                .then(function (payload) {
                  deps.onError(
                    (payload && payload.detail) || "The media cue was refused.",
                    { status: response.status },
                  );
                  return false;
                });
            },
            function (error) {
              // Reported, not locked: the projector either got the cue or it did
              // not, and pressing again is the correct and harmless response.
              deps.onError("The media cue could not be delivered.", error);
              return false;
            },
          );
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
      ownershipStatus: doc.getElementById("control-ownership-status"),
      ownershipDetail: doc.getElementById("control-ownership-detail"),
      projectors: doc.getElementById("control-projectors"),
      takeover: doc.getElementById("control-takeover"),
      ownershipRetry: doc.getElementById("control-ownership-retry"),
      takeoverModal: doc.getElementById("control-takeover-modal"),
      takeoverConfirm: doc.getElementById("control-takeover-confirm"),
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
      jumpPending: doc.getElementById("control-jump-pending"),
      media: doc.getElementById("control-media"),
    };
    // Whether this panel believes a media overlay is currently up. It is
    // deliberately local rather than read from the projection: the cue persists
    // nothing, so there is no authoritative "is the photo up" to read. It stays
    // honest because the projector clears the overlay on exactly the signal that
    // resets this flag — a changed ceremony signature, computed by the shared
    // `ceremonySignature` both surfaces use so the two cannot drift.
    var mediaShown = false;
    var lastSignature = null;
    // The last projection the server confirmed; feeds the visibility mapping,
    // the start-over modal counts, and the keyboard gating.
    var lastProjection = null;
    var stateUnusable = false;
    var stateLoadFailed = false;
    var pendingDestructiveAction = null;
    var confirmationModal = window.bootstrap.Modal.getOrCreateInstance(els.confirmationModal);
    var takeoverModal = window.bootstrap.Modal.getOrCreateInstance(els.takeoverModal);
    var confirmTakeover = null;
    var commandBusy = false;
    var commandAuthority = false;

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
      commandBusy = isBusy;
      Object.keys(buttons).forEach(function (name) {
        if (buttons[name]) {
          buttons[name].disabled = isBusy || (name !== "reload" && !commandAuthority);
        }
      });
    }

    function setCommandsEnabled(enabled) {
      commandAuthority = enabled;
      setBusy(commandBusy);
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
      buttons.jumpPending.hidden = !controls.jumpPendingVisible;
      buttons.media.hidden = !controls.mediaVisible;
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
      renderMediaButton(focused);
    }

    // The button always states what is on the projector, not only what can be
    // done next, and it names the team — "Show media" alone is a question.
    function renderMediaButton(focused) {
      if (!buttons.media) {
        return;
      }
      var name = focused ? format.teamLabel(focused) : "";
      buttons.media.textContent = mediaShown ? "Hide media" : "Show media";
      buttons.media.setAttribute(
        "title",
        name ? (mediaShown ? "Hide " + name + " on the projector" : "Show " + name + " on the projector") : "",
      );
      if (els.mediaTeam) {
        els.mediaTeam.textContent = name;
      }
    }

    var lease = window.AnimatorControlLease.createLeaseClient({
      fetchImpl: function (url, init) {
        return window.fetch(url, init);
      },
      urls: {
        claim: root.getAttribute("data-lease-claim-url"),
        heartbeat: root.getAttribute("data-lease-heartbeat-url"),
        release: root.getAttribute("data-lease-release-url"),
        takeover: root.getAttribute("data-lease-takeover-url"),
      },
      visibilityTarget: doc,
      pageTarget: window,
      getVisibility: function () {
        return doc.visibilityState;
      },
      randomUUID:
        typeof crypto !== "undefined" && crypto && typeof crypto.randomUUID === "function"
          ? crypto.randomUUID.bind(crypto)
          : null,
    });
    var ownership = window.AnimatorControlOwnership.createOwnershipController({
      lease: lease,
      elements: {
        label: els.ownershipStatus,
        detail: els.ownershipDetail,
        projectors: els.projectors,
        takeover: els.takeover,
        retry: els.ownershipRetry,
      },
      setCommandsEnabled: setCommandsEnabled,
      confirmTakeover: function (confirmed) {
        confirmTakeover = confirmed;
        takeoverModal.show();
      },
    });
    els.takeoverConfirm.addEventListener("click", function () {
      var confirmed = confirmTakeover;
      confirmTakeover = null;
      takeoverModal.hide();
      if (confirmed) {
        confirmed();
      }
    });

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
        jumpPending: root.getAttribute("data-jump-pending-url"),
        showMedia: root.getAttribute("data-show-media-url"),
        hideMedia: root.getAttribute("data-hide-media-url"),
      },
      ownership: ownership,
      onState: function (projection) {
        // Reset the label only when the ceremony actually *moved*, using the
        // projector's own definition of movement. Every command and every reload
        // lands here, but the projector closes its overlay on a changed
        // signature — so clearing the flag unconditionally would make the button
        // read "Show media" while a photograph is still on the projector, and
        // the operator's next press would re-show it instead of hiding it.
        var signature = cueApi.ceremonySignature(projection);
        if (lastSignature !== null && signature !== lastSignature) {
          mediaShown = false;
        }
        lastSignature = signature;
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
        .fetch(root.getAttribute("data-meta-url"), { headers: { Accept: "application/json" }, cache: "no-store" })
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
      client
        .loadState()
        .catch(function (error) {
          // Record the load failure for the controls mapping, but do NOT stop
          // here: claiming the lease depends only on the credential, and the
          // documented recovery for an unusable stored payload (Rebuild state,
          // i.e. start-reveal + restart) needs command authority even when the
          // state cannot be read. Skipping the claim here used to leave every
          // button disabled behind a "Use Rebuild state" message.
          stateUnusable = isUnusableStateError(error);
          stateLoadFailed = !stateUnusable;
          renderState(null);
          setError(
            stateUnusable
              ? "The stored reveal state cannot be read. Use Rebuild state to replace it."
              : "The ceremony state could not be loaded.",
          );
          return null;
        })
        .then(function () {
          if (client.hasSecret()) {
            return ownership.claim(value);
          }
          return null;
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
          "This discards the stored ceremony and rebuilds it from current contest data and settings — including the " +
          "medal cutoffs. Runs judged since the ceremony began are included, so the reveal order may differ from " +
          "what was already shown.";
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
    buttons.jumpPending.addEventListener("click", function () {
      client.jumpPending();
    });
    buttons.media.addEventListener("click", function () {
      var next = !mediaShown;
      client.mediaCue(next ? "show" : "hide").then(function (ok) {
        // Only a confirmed cue flips the label. A refused one leaves the button
        // describing what is actually on the projector, so the operator's next
        // press repeats the attempt rather than sending its opposite.
        if (ok) {
          mediaShown = next;
          renderState(lastProjection);
        }
      });
    });
    buttons.reload.addEventListener("click", function () {
      client.reload();
    });

    doc.addEventListener("keydown", function (event) {
      if (isFormControl(event.target) || !client.hasSecret() || !ownership.canCommand()) {
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
    LEASE_LOST_DETAIL: LEASE_LOST_DETAIL,
    createCommandClient: createCommandClient,
    controlsForState: controlsForState,
    isDefinitive: isDefinitive,
    isFormControl: isFormControl,
    isUnusableStateError: isUnusableStateError,
    boot: boot,
  };
});
