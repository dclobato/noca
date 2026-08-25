/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 The NOCA Authors (see AUTHORS)
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

"use strict";

/*
 * Behavioural tests for the navbar clock driver.
 *
 * The driver is what decides *when* the countdown repaints, and that is the
 * whole of the "changes colour on the second it should" contract -- something
 * no assertion about the shared utility's return values can reach. The driver
 * is loaded into a vm context with a stub DOM, a stub `fetch`, a captured
 * `setInterval` and a clock the test moves by hand, so a tick is an ordinary
 * function call rather than a second of real waiting.
 */

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "../../..");
const utilsSrc = fs.readFileSync(
  path.join(root, "shared/static/js/contest-clock-utils.js"), "utf8");
const driverSrc = fs.readFileSync(
  path.join(root, "web/static/js/contest-clock.js"), "utf8");

const SECOND = 1_000;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;

// An arbitrary "wall clock" the harness moves; only differences matter.
const T0 = 1_700_000_000_000;

/**
 * Let every pending microtask and resolved promise settle.
 *
 * `sync()` awaits twice (the response, then its body), and the stubs resolve
 * across realms, so one turn of the loop is not enough to be sure the driver
 * has finished reacting.
 */
async function flush() {
  for (let i = 0; i < 8; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

/**
 * Load the utility and the driver into a fresh context.
 *
 * `fetchImpl(callCount)` stands in for the server; the returned handle exposes
 * the two stub elements, the captured intervals, and a `setNow` to move time.
 */
function boot(fetchImpl) {
  const countdown = { textContent: "Updating...", dataset: { clockUrl: "/clock" } };
  const phase = { textContent: "", dataset: {}, hidden: false };
  const intervals = [];
  let now = T0;
  let calls = 0;

  const ctx = {
    window: {},
    document: {
      getElementById(id) {
        if (id === "contest-countdown") return countdown;
        if (id === "contest-phase") return phase;
        return null;
      },
    },
    fetch: () => fetchImpl(calls++),
    setInterval: (fn, ms) => intervals.push({ fn, ms }),
    Date: { now: () => now },
  };
  vm.createContext(ctx);
  vm.runInContext(utilsSrc, ctx);
  // In a browser `window` *is* the global, so the utility's export is reachable
  // as a bare `ContestClockUtils` -- which is exactly how the driver names it.
  // A vm sandbox has no such identity, so republish it before loading the
  // driver rather than letting it fail inside the driver's own catch.
  ctx.ContestClockUtils = ctx.window.ContestClockUtils;
  vm.runInContext(driverSrc, ctx);

  return {
    countdown,
    phase,
    intervals,
    setNow(value) { now = value; },
    /** Fire the one-second repaint interval, as the browser would. */
    tick() {
      const render = intervals.find((entry) => entry.ms === 1_000);
      assert.ok(render, "no one-second repaint interval was registered");
      render.fn();
    },
  };
}

/**
 * A well-formed clock payload for a contest starting at `T0 + startsIn` and
 * running for `duration`.
 *
 * `server_now_ms` is always T0, which is where the harness clock starts, so the
 * driver computes a zero offset and the moments below can be read directly
 * against the values `setNow` is given.
 */
function payload(startsIn, duration) {
  return {
    ok: true,
    json: async () => ({
      server_now_ms: T0,
      start_ms: T0 + startsIn,
      end_ms: T0 + startsIn + duration,
    }),
  };
}

async function main() {
  // ── A failed first sync must not announce a contest that ended in 1970 ──────
  // Both moments default to 0 before a payload lands, which every comparison
  // reads as a long-finished contest. The bar keeps its placeholder instead.
  for (const failure of [
    () => Promise.reject(new Error("offline")),
    () => Promise.resolve({ ok: false, json: async () => ({}) }),
    () => Promise.resolve({ ok: true, json: async () => ({ start_ms: null, end_ms: null }) }),
  ]) {
    const app = boot(failure);
    await flush();
    app.tick();

    assert.equal(app.countdown.textContent, "Updating...");
    assert.equal(app.countdown.dataset.urgency, undefined);
    assert.equal(app.phase.textContent, "");
  }

  // ── A good sync paints text and urgency together ───────────────────────────
  {
    const app = boot(() => payload(-HOUR, 5 * HOUR));
    await flush();

    assert.equal(app.countdown.textContent, "4h 00min until end");
    assert.equal(app.countdown.dataset.urgency, "normal");
    assert.equal(app.phase.dataset.phase, "running");
  }

  // ── Urgency advances on the one-second tick, not on the 60-second resync ───
  // This is the acceptance criterion the driver alone can satisfy: between two
  // syncs the clock must still cross 30:00 and 05:00 on the right second.
  {
    const app = boot(() => payload(0, 5 * HOUR));
    await flush();
    assert.equal(app.countdown.dataset.urgency, "normal");

    // 30:01 left -- still the resting state.
    app.setNow(T0 + 5 * HOUR - 30 * MINUTE - SECOND);
    app.tick();
    assert.equal(app.countdown.dataset.urgency, "normal");

    // Exactly 30:00 left, one tick later, with no intervening fetch.
    app.setNow(T0 + 5 * HOUR - 30 * MINUTE);
    app.tick();
    assert.equal(app.countdown.dataset.urgency, "warning");
    assert.equal(app.countdown.textContent, "30min until end");

    app.setNow(T0 + 5 * HOUR - 5 * MINUTE - SECOND);
    app.tick();
    assert.equal(app.countdown.dataset.urgency, "warning");

    app.setNow(T0 + 5 * HOUR - 5 * MINUTE);
    app.tick();
    assert.equal(app.countdown.dataset.urgency, "critical");

    // The end moment itself is still the contest; the millisecond after is not.
    app.setNow(T0 + 5 * HOUR);
    app.tick();
    assert.equal(app.countdown.dataset.urgency, "critical");
    assert.equal(app.countdown.textContent, "0s until end");

    app.setNow(T0 + 5 * HOUR + 1);
    app.tick();
    assert.equal(app.countdown.dataset.urgency, "ended");
    assert.equal(app.countdown.textContent, "The contest is over");
  }

  // ── An upcoming contest never wears a running contest's urgency ────────────
  {
    const app = boot(() => ({
      ok: true,
      json: async () => ({
        server_now_ms: T0,
        start_ms: T0 + 4 * MINUTE,
        end_ms: T0 + 4 * MINUTE + 20 * MINUTE,
      }),
    }));
    await flush();

    // Four minutes to start, in a contest whose whole run is inside the warning
    // window: neither fact may colour the wait.
    assert.equal(app.countdown.dataset.urgency, "normal");
    assert.match(app.countdown.textContent, /to start/);
    assert.equal(app.phase.dataset.phase, "upcoming");

    // The instant it starts, the running contest's own urgency applies.
    app.setNow(T0 + 4 * MINUTE);
    app.tick();
    assert.equal(app.countdown.dataset.urgency, "warning");
  }

  // ── A resync neither loses nor lags the urgency state ──────────────────────
  {
    const responses = [payload(0, 5 * HOUR), payload(MINUTE - 5 * HOUR, 5 * HOUR)];
    const app = boot((call) => responses[Math.min(call, responses.length - 1)]);
    await flush();
    assert.equal(app.countdown.dataset.urgency, "normal");

    const resync = app.intervals.find((entry) => entry.ms === 60_000);
    assert.ok(resync, "no 60-second resync interval was registered");
    resync.fn();
    await flush();

    assert.equal(app.countdown.dataset.urgency, "critical");

    // A malformed payload afterwards keeps the last good state rather than
    // turning every comparison into NaN.
    responses.push({ ok: true, json: async () => ({ server_now_ms: "soon" }) });
    resync.fn();
    await flush();

    assert.equal(app.countdown.dataset.urgency, "critical");
    assert.equal(app.countdown.textContent, "1min 00s until end");
  }
}

main().then(
  () => process.exit(0),
  (error) => {
    console.error(error);
    process.exit(1);
  },
);
