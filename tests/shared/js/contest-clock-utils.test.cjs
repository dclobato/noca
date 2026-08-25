/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 The NOCA Authors (see AUTHORS)
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const utilityPath = path.resolve(
  __dirname,
  "../../../shared/static/js/contest-clock-utils.js",
);
const context = { window: {} };
vm.createContext(context);
vm.runInContext(fs.readFileSync(utilityPath, "utf8"), context);

const { countdownText, countdownUrgency, formatDuration, contestPhase, phaseLabel } =
  context.window.ContestClockUtils;
const SECOND = 1_000;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;

assert.equal(formatDuration(90 * MINUTE, false), "1h 30min");
assert.equal(formatDuration(75 * SECOND, true), "1min 15s");

assert.equal(
  countdownText(0, 12 * HOUR, 17 * HOUR),
  "Coming soon\u2026",
);
assert.equal(
  countdownText(0, 11 * HOUR + 5 * MINUTE, 16 * HOUR),
  "11h 05min to start\u2026 and counting",
);
assert.equal(
  countdownText(0, 4 * MINUTE + 12 * SECOND, 5 * HOUR),
  "4min 12s to start\u2026 and counting",
);
assert.equal(
  countdownText(30 * MINUTE, 0, 90 * MINUTE),
  "1h 00min until end",
);
assert.equal(
  countdownText(90 * MINUTE - 75 * SECOND, 0, 90 * MINUTE),
  "1min 15s until end",
);
assert.equal(
  countdownText(90 * MINUTE, 0, 90 * MINUTE),
  "0s until end",
);
assert.equal(
  countdownText(90 * MINUTE + 1, 0, 90 * MINUTE),
  "The contest is over",
);

// ── Remaining-time urgency ───────────────────────────────────────────────────
// A separate axis from the phase below: only "how much time is left". The
// boundaries belong to the more urgent state, so a clock reading exactly 30:00
// is already amber and one reading exactly 5:00 is already red.
{
  const S = 0;
  const E = 5 * HOUR;

  // The wait for a contest to start is never urgent -- not even in its last
  // five minutes, where the countdown starts showing seconds.
  assert.equal(countdownUrgency(-3 * HOUR, S, E), "normal");
  assert.equal(countdownUrgency(-MINUTE, S, E), "normal");
  assert.equal(countdownUrgency(-SECOND, S, E), "normal");

  assert.equal(countdownUrgency(S, S, E), "normal");
  assert.equal(countdownUrgency(E - 2 * HOUR, S, E), "normal");

  // Exactly 30 minutes left is already the warning state; one millisecond more
  // than that is not.
  assert.equal(countdownUrgency(E - 30 * MINUTE - 1, S, E), "normal");
  assert.equal(countdownUrgency(E - 30 * MINUTE, S, E), "warning");
  assert.equal(countdownUrgency(E - 17 * MINUTE, S, E), "warning");
  assert.equal(countdownUrgency(E - 5 * MINUTE - 1, S, E), "warning");

  // Exactly 5 minutes left is already critical, and stays so through the end
  // moment itself -- the contest is still running on that millisecond, which is
  // the same boundary countdownText treats as "0s until end".
  assert.equal(countdownUrgency(E - 5 * MINUTE, S, E), "critical");
  assert.equal(countdownUrgency(E - SECOND, S, E), "critical");
  assert.equal(countdownUrgency(E, S, E), "critical");

  // Past the end moment the clock is spent, matching "The contest is over".
  assert.equal(countdownUrgency(E + 1, S, E), "ended");
  assert.equal(countdownUrgency(E + HOUR, S, E), "ended");

  // Urgency and countdown text agree about where the contest ends.
  assert.equal(countdownText(E, S, E), "0s until end");
  assert.equal(countdownText(E + 1, S, E), "The contest is over");

  // A contest shorter than the thresholds is urgent from its first second
  // rather than dividing by a window it never has.
  assert.equal(countdownUrgency(0, 0, 3 * MINUTE), "critical");
  assert.equal(countdownUrgency(0, 0, 20 * MINUTE), "warning");

  // Urgency is independent of phase: a frozen contest with hours to run is not
  // urgent, and a still-unfrozen one in its last minutes is.
  assert.equal(contestPhase(2 * HOUR, S, E, HOUR, null), "frozen");
  assert.equal(countdownUrgency(2 * HOUR, S, E), "normal");
  assert.equal(contestPhase(E - MINUTE, S, E, null, null), "running");
  assert.equal(countdownUrgency(E - MINUTE, S, E), "critical");
}

// ── Contest phase ────────────────────────────────────────────────────────────
// Mirrors contest_clock_state() in
// web/services/contest_service/presentation.py: the phases are ordered and the
// most advanced one that applies wins.
const START = 0;
const FREEZE = 4 * HOUR;
const BLIND = 4 * HOUR + 30 * MINUTE;
const END = 5 * HOUR;

assert.equal(contestPhase(-MINUTE, START, END, FREEZE, BLIND), "upcoming");
assert.equal(contestPhase(HOUR, START, END, FREEZE, BLIND), "running");
assert.equal(contestPhase(FREEZE + MINUTE, START, END, FREEZE, BLIND), "frozen");
assert.equal(contestPhase(BLIND + MINUTE, START, END, FREEZE, BLIND), "silence");
assert.equal(contestPhase(END + MINUTE, START, END, FREEZE, BLIND), "past");

// The boundaries themselves belong to the earlier phase.
assert.equal(contestPhase(FREEZE, START, END, FREEZE, BLIND), "running");
assert.equal(contestPhase(BLIND, START, END, FREEZE, BLIND), "frozen");
assert.equal(contestPhase(END, START, END, FREEZE, BLIND), "silence");

// Blind before freeze is a legal configuration; neither is assumed first.
assert.equal(contestPhase(3 * HOUR, START, END, 4 * HOUR, 2 * HOUR), "silence");

// A payload from a server that does not send the moments degrades cleanly.
assert.equal(contestPhase(HOUR, START, END, null, null), "running");
assert.equal(contestPhase(END + MINUTE, START, END, null, null), "past");

// Every phase the pill shows names itself; colour is never the only signal.
assert.equal(phaseLabel("running"), "Live");
assert.equal(phaseLabel("frozen"), "Scoreboard frozen");
assert.equal(phaseLabel("silence"), "No more answers");
assert.equal(phaseLabel("upcoming"), "");
assert.equal(phaseLabel("past"), "");
