//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// The projector's half of the operator's team-media cue.
//
// The operator running an award ceremony is on stage, away from the machine
// driving the projector, so `reveal_media_cue` lets them raise and lower a
// team's photo and clip from a remote. This module owns what the projector does
// about it, and nothing else: it touches no network, holds no ceremony state,
// and decides nothing about which team — the server already resolved that from
// the ceremony's own cursor.
//
// Three rules live here, each because the obvious implementation is wrong:
//
//   1. **A show opens the team's real row button, never `modal.show()`.** In
//      Bootstrap 5.3 focus *restoration* lives in the data-API click handler, so
//      a programmatic open traps focus correctly and then returns it nowhere.
//      Clicking the trigger is also what supplies `relatedTarget`, which is how
//      the modal learns which team it is showing.
//   2. **Switching teams closes first and reopens on `hidden`.** Bootstrap
//      ignores a show on an already-open dialog, and the teardown that frees the
//      previous team's media runs on hide — so the incoming team must wait for
//      that close rather than race it.
//   3. **A ceremony that moves takes the overlay down.** An operator who cues a
//      photo and then presses Step is not asking to reveal a result from behind
//      a photograph. Nothing on the server can enforce this, because the cue
//      persists no state for a later command to clear; and doing it here is what
//      lets both operator clients keep a purely local Show/Hide label, since the
//      projector and the panels reset on the same signal.
//
// Exported as UMD: `window.CeremonyMediaCue` / `module.exports`.
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.CeremonyMediaCue = api;
  }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  /**
   * The three fields that make a projection a *different* moment in the ceremony.
   *
   * Counts and focus cover every command — a step moves one or both, a back moves
   * them the other way, and reset/start move the phase. Comparing this rather
   * than the whole payload is what keeps a plain re-render, or a reconnect
   * reconciliation that refetches identical state, from reading as a change and
   * closing an overlay the operator just raised.
   *
   * **Both operator panels import this too**, and must: they reset their local
   * Show/Hide label when the ceremony moves, and the projector closes its overlay
   * on the same signal. Two definitions of "moved" would drift, and the symptom
   * would be a button describing the opposite of what is on the projector — so
   * `control.js` and the Android `ceremonySignature` are held to this one.
   */
  function ceremonySignature(projection) {
    if (!projection) {
      return "none";
    }
    return projection.phase + "|" + projection.revealed_count + "|" + (projection.focused_team_id || "");
  }

  // deps: { findTrigger, isOpen, close, currentTeamId, escape }
  //
  //   findTrigger(teamId) -> the team's row button, or null when it is not on
  //     the board (a cue that raced a re-render, or a row this scope filtered
  //     out). Doing nothing is right there: no photo would be correct to show.
  //   isOpen()            -> whether the dialog is currently displayed.
  //   close()             -> hide the dialog; the caller must invoke
  //                          `onHidden()` when Bootstrap's `hidden.bs.modal`
  //                          fires, which is what completes a team switch.
  //   currentTeamId()     -> the team the dialog is showing, or null.
  function createMediaCueController(deps) {
    var pending = null;
    var lastSignature = null;
    var openedRemotely = false;

    function open(teamId) {
      var trigger = deps.findTrigger(teamId);
      if (!trigger) {
        return false;
      }
      openedRemotely = true;
      trigger.click();
      return true;
    }

    return {
      /** Apply one cue from the stream. */
      apply: function (cue) {
        if (!cue || (cue.action !== "show" && cue.action !== "hide")) {
          return;
        }
        if (cue.action === "hide") {
          pending = null;
          if (deps.isOpen()) {
            deps.close();
          }
          return;
        }
        if (!cue.team_id) {
          return;
        }
        if (deps.isOpen()) {
          if (deps.currentTeamId() === cue.team_id) {
            return; // Already showing exactly this team; re-cueing changes nothing.
          }
          pending = cue.team_id;
          deps.close();
          return;
        }
        open(cue.team_id);
      },

      /**
       * Apply one authoritative projection, closing the overlay if it moved.
       *
       * Returns ``true`` when this projection was a real ceremony change, which
       * is also the signal a caller uses to reset its own Show/Hide label.
       */
      applyState: function (projection) {
        var signature = ceremonySignature(projection);
        var moved = lastSignature !== null && signature !== lastSignature;
        lastSignature = signature;
        if (moved) {
          pending = null;
          if (deps.isOpen()) {
            deps.close();
          }
        }
        return moved;
      },

      /** Call from `hidden.bs.modal`; completes a pending team switch. */
      onHidden: function () {
        openedRemotely = false;
        var teamId = pending;
        pending = null;
        if (teamId) {
          open(teamId);
        }
      },

      /** Whether the open now on screen came from a cue rather than a click. */
      openedRemotely: function () {
        return openedRemotely;
      },
    };
  }

  return {
    ceremonySignature: ceremonySignature,
    createMediaCueController: createMediaCueController,
  };
});
