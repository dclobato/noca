//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Browser-independent contract test for ceremony-media-cue.js: what the
// projector does when the operator raises or lowers a team's media from the
// remote. A fake dialog records opens and closes, so the three rules the module
// exists for are verified without a browser or a Bootstrap build.

"use strict";

const assert = require("assert");
const path = require("path");

const cueApi = require(path.join(__dirname, "..", "..", "..", "animator", "static", "js", "ceremony-media-cue.js"));

// A stand-in for the board plus the one reused dialog. `triggers` is the set of
// teams currently rendered as rows; anything else has no button to click.
function harness(triggers = ["t1", "t2"]) {
  const log = [];
  let open = null;
  const controller = cueApi.createMediaCueController({
    findTrigger(teamId) {
      if (triggers.indexOf(teamId) === -1) {
        return null;
      }
      return {
        click() {
          log.push("open:" + teamId);
          open = teamId;
        },
      };
    },
    isOpen: () => open !== null,
    close() {
      log.push("close:" + open);
      open = null;
      // Bootstrap fires `hidden.bs.modal` after the transition; the real page
      // wires that to onHidden(), which is what completes a team switch.
      controller.onHidden();
    },
    currentTeamId: () => open,
  });
  return {
    controller,
    log,
    isOpen: () => open !== null,
    current: () => open,
  };
}

// `applyState` and `ceremonySignature` both take a *projection*, not the
// `/reveal/state` envelope: the operator panels hold a bare projection and share
// the same signature, so one shape serves all three callers.
function state(phase, revealed, focused) {
  return { phase, revealed_count: revealed, focused_team_id: focused };
}

// ── A show opens the team's own row button ──────────────────────────────────
function testShowClicksTheRealTrigger() {
  const h = harness();
  h.controller.apply({ action: "show", team_id: "t1" });

  // Clicking the trigger, rather than calling modal.show(), is what preserves
  // Bootstrap 5.3's focus restoration (which lives in the data-API click
  // handler) and what supplies `relatedTarget` so the modal knows the team.
  assert.deepStrictEqual(h.log, ["open:t1"]);
  assert.strictEqual(h.current(), "t1");
  assert.strictEqual(h.controller.openedRemotely(), true);
}

function testHideClosesTheDialog() {
  const h = harness();
  h.controller.apply({ action: "show", team_id: "t1" });
  h.controller.apply({ action: "hide", team_id: null });

  assert.deepStrictEqual(h.log, ["open:t1", "close:t1"]);
  assert.strictEqual(h.isOpen(), false);
}

// ── Repetition is harmless in both directions ───────────────────────────────
function testRepeatedCuesAreNoOps() {
  const h = harness();
  h.controller.apply({ action: "show", team_id: "t1" });
  h.controller.apply({ action: "show", team_id: "t1" });
  h.controller.apply({ action: "show", team_id: "t1" });

  // Re-cueing the team already on screen changes nothing — which is precisely
  // why the server needs no idempotency key for this command.
  assert.deepStrictEqual(h.log, ["open:t1"]);

  h.controller.apply({ action: "hide", team_id: null });
  h.controller.apply({ action: "hide", team_id: null });
  assert.deepStrictEqual(h.log, ["open:t1", "close:t1"], "hiding twice closes once");
}

// ── Switching teams closes first, then reopens ──────────────────────────────
function testSwitchingTeamsWaitsForTheClose() {
  const h = harness();
  h.controller.apply({ action: "show", team_id: "t1" });
  h.controller.apply({ action: "show", team_id: "t2" });

  // Bootstrap ignores a show on an open dialog, and the teardown that frees the
  // previous team's media runs on hide — so the incoming team must wait for the
  // close rather than race it.
  assert.deepStrictEqual(h.log, ["open:t1", "close:t1", "open:t2"]);
  assert.strictEqual(h.current(), "t2");
}

// ── A team that is not on the board is ignored, not thrown over ─────────────
function testUnknownTeamIsIgnored() {
  const h = harness(["t1"]);
  h.controller.apply({ action: "show", team_id: "ghost" });

  // A cue that raced a re-render, or a row this scope filtered out. There is no
  // photo this projector could correctly show, so doing nothing is right.
  assert.deepStrictEqual(h.log, []);
  assert.strictEqual(h.isOpen(), false);
}

function testMalformedCuesAreIgnored() {
  const h = harness();
  h.controller.apply(null);
  h.controller.apply({});
  h.controller.apply({ action: "sideways", team_id: "t1" });
  h.controller.apply({ action: "show", team_id: null });

  assert.deepStrictEqual(h.log, []);
}

// ── A ceremony that moves takes the overlay down ────────────────────────────
function testCeremonyMovementClosesTheOverlay() {
  const h = harness();
  h.controller.applyState(state("revealing", 3, "t1"));
  h.controller.apply({ action: "show", team_id: "t1" });

  // An operator who cues a photo and then presses Step is not asking to reveal a
  // result from behind a photograph. Nothing on the server can enforce this: the
  // cue persists no state for a later command to clear.
  const moved = h.controller.applyState(state("revealing", 4, "t1"));
  assert.strictEqual(moved, true);
  assert.deepStrictEqual(h.log, ["open:t1", "close:t1"]);
}

function testUnchangedStateLeavesTheOverlayAlone() {
  const h = harness();
  const current = state("revealing", 3, "t1");
  h.controller.applyState(current);
  h.controller.apply({ action: "show", team_id: "t1" });

  // A reconnect reconciliation refetches identical state, and a plain re-render
  // reapplies it. Neither is a ceremony change, and neither has any business
  // closing an overlay the operator just raised.
  assert.strictEqual(h.controller.applyState(state("revealing", 3, "t1")), false);
  assert.strictEqual(h.controller.applyState(current), false);
  assert.deepStrictEqual(h.log, ["open:t1"]);
  assert.strictEqual(h.isOpen(), true);
}

function testTheFirstStateIsNeverAMovement() {
  const h = harness();
  // Nothing can be open before the first projection arrives, and treating the
  // initial load as a change would make the signature meaningless.
  assert.strictEqual(h.controller.applyState(state("revealing", 3, "t1")), false);
  assert.strictEqual(h.controller.applyState(null), true);
}

function testEveryCommandShapeCountsAsMovement() {
  // Counts and focus cover step and back; phase covers reset and start.
  const cases = [
    [state("revealing", 3, "t1"), state("revealing", 4, "t1"), "a step"],
    [state("revealing", 4, "t1"), state("revealing", 3, "t1"), "a back"],
    [state("revealing", 3, "t1"), state("revealing", 3, "t2"), "a cursor move"],
    [state("revealing", 3, "t1"), state("done", 3, "t1"), "the ceremony finishing"],
    [state("revealing", 3, "t1"), state("idle", 3, "t1"), "a reset"],
  ];
  cases.forEach(([before, after, label]) => {
    const h = harness();
    h.controller.applyState(before);
    assert.strictEqual(h.controller.applyState(after), true, label + " must count as movement");
  });
}

// ── A pending switch is abandoned when the ceremony moves ───────────────────
function testMovementCancelsAPendingSwitch() {
  const log = [];
  let open = "t1";
  let pendingHidden = null;
  const controller = cueApi.createMediaCueController({
    findTrigger: (teamId) => ({
      click() {
        log.push("open:" + teamId);
        open = teamId;
      },
    }),
    isOpen: () => open !== null,
    close() {
      log.push("close:" + open);
      open = null;
      // A real Bootstrap close is asynchronous, so hold `hidden` until the test
      // releases it — which is where a cancelled switch can be observed.
      pendingHidden = () => controller.onHidden();
    },
    currentTeamId: () => open,
  });

  controller.apply({ action: "show", team_id: "t2" }); // queues a switch
  controller.applyState(state("revealing", 1, "t1"));
  controller.applyState(state("revealing", 2, "t1")); // the ceremony moved
  pendingHidden();

  // The switch was to a team the ceremony has since moved past; reopening it
  // after the board changed would put a stale face on the projector.
  assert.deepStrictEqual(log, ["close:t1"]);
  assert.strictEqual(open, null);
}

// ── openedRemotely tracks how the current open happened ─────────────────────
function testOpenedRemotelyResetsOnHidden() {
  const h = harness();
  assert.strictEqual(h.controller.openedRemotely(), false);
  h.controller.apply({ action: "show", team_id: "t1" });
  assert.strictEqual(h.controller.openedRemotely(), true);
  h.controller.apply({ action: "hide", team_id: null });
  // A later local click must not be reported as remote, which is what decides
  // whether the audience-facing copy or the operator hint is shown.
  assert.strictEqual(h.controller.openedRemotely(), false);
}

function testSignatureIsStableForEquivalentStates() {
  const sig = cueApi.ceremonySignature;
  assert.strictEqual(sig(state("revealing", 3, "t1")), sig(state("revealing", 3, "t1")));
  assert.strictEqual(sig(null), "none");
  assert.strictEqual(sig(undefined), "none");
  assert.notStrictEqual(sig(state("revealing", 3, "t1")), sig(state("revealing", 3, null)));

  // Pinned against the Kotlin port in `core/Controls.kt`, which must produce
  // this exact string: the projector, the web panel, and the remote all decide
  // "did the ceremony move" from it, and a drift shows up as a button
  // describing the opposite of what is on the projector.
  assert.strictEqual(sig(state("revealing", 3, "t1")), "revealing|3|t1");
  assert.strictEqual(sig(state("done", 7, null)), "done|7|");
}

testShowClicksTheRealTrigger();
testHideClosesTheDialog();
testRepeatedCuesAreNoOps();
testSwitchingTeamsWaitsForTheClose();
testUnknownTeamIsIgnored();
testMalformedCuesAreIgnored();
testCeremonyMovementClosesTheOverlay();
testUnchangedStateLeavesTheOverlayAlone();
testTheFirstStateIsNeverAMovement();
testEveryCommandShapeCountsAsMovement();
testMovementCancelsAPendingSwitch();
testOpenedRemotelyResetsOnHidden();
testSignatureIsStableForEquivalentStates();
console.log("ceremony-media-cue contract: OK");
