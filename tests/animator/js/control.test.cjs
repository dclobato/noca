//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Browser-independent contract test for control.js. A fake fetch drives the real
// command client, pinning the two properties that matter most:
//
//   - the operator secret travels only in an Authorization header, and never in
//     a URL, a body, or any storage;
//   - an ambiguous outcome (network failure, 5xx) keeps the controls locked
//     until authoritative state is reloaded, so a failed `step` can never be
//     replayed into a double reveal.

"use strict";

const assert = require("assert");
const path = require("path");

const controlApi = require(path.join(__dirname, "..", "..", "..", "animator", "static", "js", "control.js"));

const URLS = {
  state: "/c/x/control/state",
  start: "/c/x/control/start-reveal",
  step: "/c/x/control/step",
  back: "/c/x/control/back",
  reset: "/c/x/control/reset",
  jump: "/c/x/control/jump-team",
  jumpPending: "/c/x/control/jump-pending",
};

const PROJECTION = {
  contest_id: "c1",
  scope: "global",
  site_id: null,
  site_name: null,
  phase: "revealing",
  focused_team_id: "t1",
  revealed_count: 2,
  frozen_count: 7,
  medal_cutoffs: null,
  teams: [],
};

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  };
}

// `plan` maps a URL to a queue of responses (or Error instances to reject with).
function makeHarness(plan) {
  const calls = [];
  const events = { states: [], errors: [], statuses: [], busy: [], auth: 0, reconcileFailures: 0 };
  const client = controlApi.createCommandClient({
    fetchImpl(url, init) {
      calls.push({ url, init });
      const queue = plan[url] || [];
      const next = queue.length > 1 ? queue.shift() : queue[0];
      if (next instanceof Error) {
        return Promise.reject(next);
      }
      return Promise.resolve(next);
    },
    urls: URLS,
    ownership: {
      canCommand: () => true,
      controllerHeader: () => "controller-test-id",
      markLost: () => events.statuses.push("lease-lost"),
    },
    onState: (p) => events.states.push(p),
    onStatus: (s) => events.statuses.push(s),
    onError: (m) => events.errors.push(m),
    setBusy: (b) => events.busy.push(b),
    onAuthFailure: () => (events.auth += 1),
    onReconcileFailure: () => (events.reconcileFailures += 1),
  });
  return { client, calls, events };
}

// ── The secret is a header, never a URL or a body ────────────────────────────
async function testSecretTravelsOnlyInTheHeader() {
  const h = makeHarness({
    [URLS.state]: [jsonResponse(200, PROJECTION)],
    [URLS.step]: [jsonResponse(200, PROJECTION)],
  });
  h.client.setSecret("super-secret-token");
  await h.client.loadState();
  await h.client.step();

  assert.ok(h.calls.length >= 2);
  h.calls.forEach((call) => {
    assert.strictEqual(call.init.headers.Authorization, "Bearer super-secret-token");
    assert.ok(call.url.indexOf("super-secret-token") === -1, "the secret is never in a URL");
    assert.ok(
      !call.init.body || call.init.body.indexOf("super-secret-token") === -1,
      "the secret is never in a body",
    );
  });
}

// ── start-reveal must carry the scope the token authorizes ───────────────────
async function testStartSendsGlobalAndSiteScopes() {
  const h = makeHarness({ [URLS.start]: [jsonResponse(200, PROJECTION)] });
  h.client.setSecret("s");

  await h.client.start("", false);
  assert.deepStrictEqual(JSON.parse(h.calls[0].init.body), { site_id: null, restart: false },
    "the global ceremony sends an explicit null site_id");

  await h.client.start("site-7", true);
  assert.deepStrictEqual(JSON.parse(h.calls[1].init.body), { site_id: "site-7", restart: true },
    "a site ceremony sends its site id, which the server compares to the token scope");
}

async function testJumpSendsTheTeamId() {
  const h = makeHarness({ [URLS.jump]: [jsonResponse(200, PROJECTION)] });
  h.client.setSecret("s");
  await h.client.jump("team-9");
  assert.deepStrictEqual(JSON.parse(h.calls[0].init.body), { team_id: "team-9" });
}

async function testScopeFreeCommandsSendNoBody() {
  const h = makeHarness({
    [URLS.step]: [jsonResponse(200, PROJECTION)],
    [URLS.back]: [jsonResponse(200, PROJECTION)],
    [URLS.reset]: [jsonResponse(200, PROJECTION)],
    [URLS.jumpPending]: [jsonResponse(200, PROJECTION)],
  });
  h.client.setSecret("s");
  await h.client.step();
  await h.client.back();
  await h.client.reset();
  await h.client.jumpPending();
  // The server's models forbid extra fields; sending nothing keeps them 200s.
  h.calls.forEach((call) => assert.strictEqual(call.init.body, undefined));
}

// ── Ten-step controls reuse the existing commands serially ──────────────────
async function testTenStepSequencesReuseSingleCommandEndpoints() {
  const h = makeHarness({
    [URLS.step]: [jsonResponse(200, PROJECTION)],
    [URLS.back]: [jsonResponse(200, PROJECTION)],
  });
  h.client.setSecret("s");

  await h.client.stepMany(10);
  await h.client.backMany(10);

  assert.strictEqual(h.calls.filter((call) => call.url === URLS.step).length, 10);
  assert.strictEqual(h.calls.filter((call) => call.url === URLS.back).length, 10);
  h.calls.forEach((call) => assert.strictEqual(call.init.body, undefined));
  assert.strictEqual(h.events.busy[0], true);
  assert.strictEqual(h.events.busy[h.events.busy.length - 1], false);
  assert.strictEqual(h.events.statuses[h.events.statuses.length - 1], "");
}

async function testTenStepSequenceNeverOverlapsRequests() {
  let active = 0;
  let maximumActive = 0;
  let calls = 0;
  const client = controlApi.createCommandClient({
    fetchImpl() {
      calls += 1;
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      return new Promise((resolve) => {
        setTimeout(() => {
          active -= 1;
          resolve(jsonResponse(200, PROJECTION));
        }, 0);
      });
    },
    urls: URLS,
    ownership: {
      canCommand: () => true,
      controllerHeader: () => "controller-test-id",
      markLost() {},
    },
    onState() {},
    onStatus() {},
    onError() {},
    setBusy() {},
    onAuthFailure() {},
    onReconcileFailure() {},
  });
  client.setSecret("s");

  await client.stepMany(10);

  assert.strictEqual(calls, 10);
  assert.strictEqual(maximumActive, 1, "the next step starts only after the previous response");
}

async function testSequenceStopsAtFirstDefinitiveFailure() {
  const h = makeHarness({
    [URLS.step]: [
      jsonResponse(200, PROJECTION),
      jsonResponse(200, PROJECTION),
      jsonResponse(409, { detail: "already done" }),
    ],
  });
  h.client.setSecret("s");

  const result = await h.client.stepMany(10);

  assert.strictEqual(result, null);
  assert.strictEqual(h.calls.filter((call) => call.url === URLS.step).length, 3);
  assert.deepStrictEqual(h.events.errors, ["already done"]);
  assert.strictEqual(h.events.busy[h.events.busy.length - 1], false);
}

async function testSequenceStopsAfterAmbiguousOutcomeReconciliation() {
  const h = makeHarness({
    [URLS.step]: [new Error("network down")],
    [URLS.state]: [jsonResponse(200, PROJECTION)],
  });
  h.client.setSecret("s");

  await h.client.stepMany(10);

  assert.strictEqual(
    h.calls.filter((call) => call.url === URLS.step).length,
    1,
    "an unknown first outcome never sends the remaining nine commands",
  );
  assert.strictEqual(h.calls.filter((call) => call.url === URLS.state).length, 0);
  assert.strictEqual(h.client.isBlocked(), true, "an unknown outcome requires an explicit reload");
  assert.strictEqual(h.events.reconcileFailures, 1);

  await h.client.reload();
  assert.strictEqual(h.calls.filter((call) => call.url === URLS.state).length, 1);
  assert.strictEqual(
    h.client.isBlocked(),
    false,
    "the operator's reload unlocks later manual commands",
  );
}

// ── Initial state load, including "no ceremony yet" ──────────────────────────
async function testInitialStateLoadAndMissingSession() {
  const ok = makeHarness({ [URLS.state]: [jsonResponse(200, PROJECTION)] });
  ok.client.setSecret("s");
  await ok.client.loadState();
  assert.deepStrictEqual(ok.events.states, [PROJECTION]);

  const none = makeHarness({ [URLS.state]: [jsonResponse(404, { detail: "no session" })] });
  none.client.setSecret("s");
  await none.client.loadState();
  assert.deepStrictEqual(none.events.states, [null], "404 is the legitimate 'no ceremony yet' state");
}

// ── Definitive refusals re-enable immediately ────────────────────────────────
async function testDefinitiveRefusalsReEnable() {
  assert.strictEqual(controlApi.isDefinitive(409), true);
  assert.strictEqual(controlApi.isDefinitive(422), true);
  assert.strictEqual(controlApi.isDefinitive(503), false);
  assert.strictEqual(controlApi.isDefinitive(undefined), false);

  const h = makeHarness({ [URLS.step]: [jsonResponse(409, { detail: "not started" })] });
  h.client.setSecret("s");
  await h.client.step();

  assert.deepStrictEqual(h.events.errors, ["not started"], "the server's detail is surfaced verbatim");
  assert.strictEqual(h.events.busy[h.events.busy.length - 1], false, "controls re-enable at once");
  assert.strictEqual(h.client.isBlocked(), false);
  assert.strictEqual(h.calls.length, 1, "a stated refusal needs no reconciliation");
}

async function testLeaseLostRefusalDoesNotEngageTheAmbiguityLock() {
  // A lease-lost `409` is *stated*: nothing was applied. Treating it like an
  // ambiguous outcome used to leave a panel that re-took control silently
  // no-oping every press until someone thought to hit Reload state.
  const h = makeHarness({
    [URLS.step]: [
      jsonResponse(409, { detail: controlApi.LEASE_LOST_DETAIL }),
      jsonResponse(200, PROJECTION),
    ],
  });
  h.client.setSecret("s");
  await h.client.step();

  assert.strictEqual(h.client.isBlocked(), false, "a stated ownership refusal is not ambiguous");
  assert.ok(h.events.statuses.includes("lease-lost"), "ownership loss reached the ownership controller");
  assert.deepStrictEqual(h.events.errors, [controlApi.LEASE_LOST_DETAIL]);

  // After the operator takes over again (canCommand true once more), the very
  // same client must actually send the next command instead of no-oping.
  await h.client.step();
  assert.strictEqual(h.calls.filter((call) => call.url === URLS.step).length, 2, "commands fire after takeover");
}

// ── Ambiguous outcomes stay locked until reconciliation ──────────────────────
async function testNetworkFailureBlocksUntilReconciled() {
  const h = makeHarness({
    [URLS.step]: [new Error("network down")],
    [URLS.state]: [jsonResponse(200, PROJECTION)],
  });
  h.client.setSecret("s");
  await h.client.step();

  assert.ok(
    !h.calls.some((c) => c.url === URLS.state),
    "an unknown outcome waits for an explicit state reload",
  );
  assert.ok(h.events.statuses.includes(controlApi.UNKNOWN_OUTCOME));
  assert.strictEqual(
    h.client.isBlocked(),
    true,
    "controls remain locked until the operator acknowledges the outcome",
  );
  assert.strictEqual(h.events.reconcileFailures, 1, "the persistent reload instruction is shown");

  await h.client.reload();
  assert.strictEqual(
    h.client.isBlocked(),
    false,
    "commands resume only after the explicit reload succeeds",
  );
  assert.ok(
    h.events.statuses.includes(controlApi.RELOADING_STATE),
    "manual reload describes the read in progress without repeating the unknown-outcome warning",
  );
  assert.strictEqual(h.events.busy[h.events.busy.length - 1], false);
}

async function test503IsTreatedAsAmbiguous() {
  const h = makeHarness({
    [URLS.step]: [jsonResponse(503, { detail: "store unavailable" })],
    [URLS.state]: [jsonResponse(200, PROJECTION)],
  });
  h.client.setSecret("s");
  await h.client.step();

  // A 503 can arrive after a fenced save already committed, so the panel must
  // never present it as a guaranteed no-op.
  assert.ok(
    !h.calls.some((c) => c.url === URLS.state),
    "a 503 is not auto-reconciled or assumed safe",
  );
  assert.strictEqual(h.client.isBlocked(), true);
  h.events.errors.forEach((message) => {
    assert.ok(!/safe to retry|nothing was applied|no-?op/i.test(message), "no message claims the 503 was a no-op");
  });
}

async function testNoSecondCommandDuringReconciliation() {
  let releaseState;
  const pendingState = new Promise((resolve) => {
    releaseState = resolve;
  });
  const h = makeHarness({
    [URLS.step]: [new Error("network down"), jsonResponse(200, PROJECTION)],
    [URLS.state]: [{ ok: true, status: 200, json: () => pendingState }],
  });
  h.client.setSecret("s");

  await h.client.step();
  assert.strictEqual(h.client.isBlocked(), true, "the panel is locked while the outcome is unknown");

  // The operator presses step again while reconciliation is still in flight.
  const stepCallsBefore = h.calls.filter((c) => c.url === URLS.step).length;
  await h.client.step();
  const stepCallsAfter = h.calls.filter((c) => c.url === URLS.step).length;
  assert.strictEqual(stepCallsAfter, stepCallsBefore, "the second step is NOT sent — no double reveal");

  const reload = h.client.reload();
  await new Promise((r) => setTimeout(r, 0));
  releaseState(PROJECTION);
  await reload;
  assert.strictEqual(h.client.isBlocked(), false, "commands resume once state is authoritative again");
}

async function testFailedReconciliationKeepsControlsLocked() {
  const h = makeHarness({
    [URLS.step]: [new Error("network down")],
    [URLS.state]: [new Error("still down")],
  });
  h.client.setSecret("s");
  await h.client.step();

  assert.strictEqual(h.client.isBlocked(), true, "a stuck-but-honest panel beats one that double-steps");
  assert.strictEqual(h.events.reconcileFailures, 1, "the operator is offered an explicit reload");

  await h.client.reload();
  assert.strictEqual(h.client.isBlocked(), true, "a failed explicit reload keeps the panel locked");
  assert.strictEqual(h.events.reconcileFailures, 2);
}

// ── A wrong secret is rejected at unlock, not just on a later command ────────
async function testForbiddenDuringInitialUnlock() {
  const h = makeHarness({ [URLS.state]: [jsonResponse(403, { detail: "Invalid operator credential" })] });
  h.client.setSecret("wrong-from-the-start");

  // The unlock path must not surface a wrong secret as a generic "state could
  // not be loaded", leaving an invalid credential in memory behind a panel that
  // looks unlocked.
  await h.client.loadState();

  assert.strictEqual(h.events.auth, 1, "the operator is re-prompted");
  assert.strictEqual(h.client.hasSecret(), false, "the invalid secret is discarded");
  assert.deepStrictEqual(h.events.states, [], "no ceremony state is rendered");

  const before = h.calls.length;
  await h.client.step();
  assert.strictEqual(h.calls.length, before, "no command is attempted without a secret");
}

// ── 403 discards the secret ──────────────────────────────────────────────────
async function testForbiddenDiscardsTheSecret() {
  const h = makeHarness({ [URLS.step]: [jsonResponse(403, { detail: "Invalid operator credential" })] });
  h.client.setSecret("wrong");
  await h.client.step();

  assert.strictEqual(h.events.auth, 1);
  assert.strictEqual(h.client.hasSecret(), false, "an invalid secret is forgotten, forcing re-entry");
  // With no secret held, further commands are not even attempted.
  const before = h.calls.length;
  await h.client.step();
  assert.strictEqual(h.calls.length, before);
}

// ── The panel shows only the controls the current phase makes meaningful ─────
function testControlsForState() {
  const controls = controlApi.controlsForState;

  // No ceremony: only Start can do anything.
  assert.deepStrictEqual(controls(null), {
    startVisible: true,
    startOverVisible: false,
    resetVisible: false,
    stepVisible: false,
    backVisible: false,
    jumpVisible: false,
    jumpPendingVisible: false,
    mediaVisible: false,
  });

  // A *stored* idle ceremony (reset to idle, or freshly created) preserves the
  // snapshot and makes Start available again. Start over stays offered too: it
  // is the only command that rebuilds from current settings, so without it a
  // changed medal cutoff could never be adopted from an idle session.
  assert.deepStrictEqual(controls({ phase: "idle" }), {
    startVisible: true,
    startOverVisible: true,
    resetVisible: false,
    stepVisible: false,
    backVisible: false,
    jumpVisible: false,
    jumpPendingVisible: false,
    mediaVisible: false,
  });

  // An unreadable stored session cannot use ordinary ceremony commands.
  // Permanently visible recovery controls sit outside this state mapping.
  assert.deepStrictEqual(controls(null, true), {
    startVisible: false,
    startOverVisible: false,
    resetVisible: false,
    stepVisible: false,
    backVisible: false,
    jumpVisible: false,
    jumpPendingVisible: false,
    mediaVisible: false,
  });

  // A transient or unknown load failure offers read-only Reload state through
  // the failure handler; ordinary ceremony commands remain hidden meanwhile.
  assert.deepStrictEqual(controls(null, false, true), {
    startVisible: false,
    startOverVisible: false,
    resetVisible: false,
    stepVisible: false,
    backVisible: false,
    jumpVisible: false,
    jumpPendingVisible: false,
    mediaVisible: false,
  });

  // Revealing with nothing revealed yet: Back stays visible — a step can be a
  // pure cursor move, so "0 revealed" does not mean "nothing to undo".
  assert.deepStrictEqual(controls({ phase: "revealing", revealed_count: 0 }), {
    startVisible: false,
    startOverVisible: true,
    resetVisible: true,
    stepVisible: true,
    backVisible: true,
    jumpVisible: true,
    jumpPendingVisible: true,
    mediaVisible: false,
  });

  // Mid-ceremony: everything but Start.
  assert.deepStrictEqual(controls({
    phase: "revealing",
    revealed_count: 3,
    frozen_count: 7,
    next_cell: { team_id: "t1", problem_id: "p1", label: "A" },
  }), {
    startVisible: false,
    startOverVisible: true,
    resetVisible: true,
    stepVisible: true,
    backVisible: true,
    jumpVisible: true,
    jumpPendingVisible: false,
    mediaVisible: false,
  });

  // Done: nothing left to step to or jump to, but the ceremony can still be
  // walked back or started over.
  assert.deepStrictEqual(controls({ phase: "done", revealed_count: 7, frozen_count: 7 }), {
    startVisible: false,
    startOverVisible: true,
    resetVisible: true,
    stepVisible: false,
    backVisible: true,
    jumpVisible: false,
    jumpPendingVisible: false,
    mediaVisible: false,
  });
}

function testUnusableStateDetectionIsExact() {
  assert.strictEqual(
    controlApi.isUnusableStateError({
      status: 500,
      detail: controlApi.UNUSABLE_STATE_DETAIL,
    }),
    true,
  );
  assert.strictEqual(
    controlApi.isUnusableStateError({
      status: 503,
      detail: "The reveal session store is unavailable; retry shortly.",
    }),
    false,
    "temporary store failures must not offer destructive rebuilding",
  );
  assert.strictEqual(
    controlApi.isUnusableStateError({ status: 500, detail: "Internal Server Error" }),
    false,
    "an unrelated server bug must not be presented as corrupt-state recovery",
  );
}

// ── Keyboard shortcuts never fire inside form controls ───────────────────────
function testShortcutSuppression() {
  assert.strictEqual(controlApi.isFormControl({ tagName: "INPUT" }), true);
  assert.strictEqual(controlApi.isFormControl({ tagName: "SELECT" }), true);
  assert.strictEqual(controlApi.isFormControl({ tagName: "TEXTAREA" }), true);
  assert.strictEqual(controlApi.isFormControl({ tagName: "BUTTON" }), true);
  assert.strictEqual(controlApi.isFormControl({ tagName: "DIV", isContentEditable: true }), true);
  assert.strictEqual(controlApi.isFormControl({ tagName: "DIV" }), false);
  assert.strictEqual(controlApi.isFormControl(null), false);
}

// ── The operator sees team names, not logins ─────────────────────────────────
function testOperatorSeesTeamNames() {
  const format = require(path.join(__dirname, "..", "..", "..", "animator", "static", "js", "cell-format.js"));
  // The panel and the projector must name a team the same way, so both read this
  // one rule rather than each formatting a row of the same projection.
  assert.strictEqual(format.teamLabel({ team_name: "usp01", team_fullname: "Unicamp Alpha" }), "Unicamp Alpha");
  assert.strictEqual(format.teamLabel({ team_name: "usp01", team_fullname: "" }), "usp01");
  assert.strictEqual(format.teamLabel({ team_name: "usp01" }), "usp01");
  assert.strictEqual(format.teamLabel(null), "");

  const source = require("fs").readFileSync(
    path.join(__dirname, "..", "..", "..", "animator", "static", "js", "control.js"),
    "utf-8",
  );
  const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  assert.ok(
    !/\.team_name\b/.test(code),
    "control.js must not render a team's login directly — it goes through teamLabel",
  );
}


// ── Every command attempt is idempotency-keyed ───────────────────────────────
const KEY_PATTERN = /^[A-Za-z0-9_-]{8,128}$/;

async function testMutatingCommandsCarryAFreshIdempotencyKey() {
  const h = makeHarness({
    [URLS.state]: [jsonResponse(200, PROJECTION)],
    [URLS.step]: [jsonResponse(200, PROJECTION)],
    [URLS.back]: [jsonResponse(200, PROJECTION)],
    [URLS.reset]: [jsonResponse(200, PROJECTION)],
    [URLS.start]: [jsonResponse(200, PROJECTION)],
    [URLS.jump]: [jsonResponse(200, PROJECTION)],
    [URLS.jumpPending]: [jsonResponse(200, PROJECTION)],
  });
  h.client.setSecret("s");

  await h.client.loadState();
  await h.client.start("", false);
  await h.client.step();
  await h.client.back();
  await h.client.reset();
  await h.client.jump("team-1");
  await h.client.jumpPending();

  const read = h.calls[0];
  assert.strictEqual(
    read.init.headers["Idempotency-Key"],
    undefined,
    "a read carries no key: there is nothing to apply twice",
  );
  assert.strictEqual(read.init.headers["X-Animator-Controller-Id"], undefined);

  const keys = h.calls.slice(1).map((call) => call.init.headers["Idempotency-Key"]);
  assert.strictEqual(keys.length, 6, "all six mutating commands were sent");
  keys.forEach((key) => {
    assert.ok(KEY_PATTERN.test(key), "the key must satisfy the server pattern, got " + key);
  });
  assert.strictEqual(new Set(keys).size, keys.length, "each attempt gets its own key");
  h.calls.slice(1).forEach((call) => {
    assert.strictEqual(call.init.headers["X-Animator-Controller-Id"], "controller-test-id");
  });
}

async function testEachRepeatedStepGetsItsOwnKey() {
  // Two deliberate presses are two commands: sharing a key would make the
  // second one replay the first and silently stall the ceremony.
  const h = makeHarness({ [URLS.step]: [jsonResponse(200, PROJECTION)] });
  h.client.setSecret("s");
  await h.client.step();
  await h.client.step();

  assert.notStrictEqual(
    h.calls[0].init.headers["Idempotency-Key"],
    h.calls[1].init.headers["Idempotency-Key"],
  );
}

async function testSequenceStepsAreDistinctlyKeyed() {
  const h = makeHarness({ [URLS.step]: [jsonResponse(200, PROJECTION)] });
  h.client.setSecret("s");
  await h.client.stepMany(3);

  const keys = h.calls.map((call) => call.init.headers["Idempotency-Key"]);
  assert.strictEqual(keys.length, 3);
  assert.strictEqual(new Set(keys).size, 3, "a sequence is three commands, not one repeated");
}

// ── The module never touches persistent browser storage ──────────────────────
function testNoPersistentStorage() {
  const source = require("fs").readFileSync(
    path.join(__dirname, "..", "..", "..", "animator", "static", "js", "control.js"),
    "utf-8",
  );
  // Comments legitimately *name* these APIs to explain why they are not used, so
  // strip them and scan the executable code only.
  const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  ["localStorage", "sessionStorage", "document.cookie", "indexedDB"].forEach((api) => {
    assert.ok(code.indexOf(api) === -1, "control.js must never use " + api);
  });
}

(async function main() {
  await testSecretTravelsOnlyInTheHeader();
  await testStartSendsGlobalAndSiteScopes();
  await testJumpSendsTheTeamId();
  await testScopeFreeCommandsSendNoBody();
  await testTenStepSequencesReuseSingleCommandEndpoints();
  await testTenStepSequenceNeverOverlapsRequests();
  await testSequenceStopsAtFirstDefinitiveFailure();
  await testSequenceStopsAfterAmbiguousOutcomeReconciliation();
  await testInitialStateLoadAndMissingSession();
  await testDefinitiveRefusalsReEnable();
  await testLeaseLostRefusalDoesNotEngageTheAmbiguityLock();
  await testNetworkFailureBlocksUntilReconciled();
  await test503IsTreatedAsAmbiguous();
  await testNoSecondCommandDuringReconciliation();
  await testFailedReconciliationKeepsControlsLocked();
  await testForbiddenDuringInitialUnlock();
  await testForbiddenDiscardsTheSecret();
  testOperatorSeesTeamNames();
  testControlsForState();
  testUnusableStateDetectionIsExact();
  testShortcutSuppression();
  testNoPersistentStorage();
  await testMutatingCommandsCarryAFreshIdempotencyKey();
  await testEachRepeatedStepGetsItsOwnKey();
  await testSequenceStepsAreDistinctlyKeyed();
  console.log("control contract: OK");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
