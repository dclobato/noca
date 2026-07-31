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

const { countdownText, formatDuration } = context.window.ContestClockUtils;
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
