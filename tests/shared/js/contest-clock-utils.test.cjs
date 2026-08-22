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

const { countdownText, formatDuration, contestPhase, phaseLabel } =
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
