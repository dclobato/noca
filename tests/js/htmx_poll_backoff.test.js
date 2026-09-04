//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const SCRIPT = path.resolve(__dirname, "..", "..", "shared", "static", "js", "htmx-poll-backoff.js");

// The script registers two document listeners and keeps its state closed over,
// so the harness drives it exactly as htmx would: dispatch an error event, then
// dispatch a request event and see whether it was cancelled.
function setup() {
    const listeners = {};
    const dispatched = [];
    global.document = {
        addEventListener: (name, fn) => { listeners[name] = fn; },
        dispatchEvent: (event) => { dispatched.push(event); return true; },
    };
    global.CustomEvent = function (type, init) { return { type, detail: (init || {}).detail }; };
    new Function(fs.readFileSync(SCRIPT, "utf8"))();

    const element = (trigger) => ({ getAttribute: (name) => (name === "hx-trigger" ? trigger : null) });
    const xhr = (status, retryAfter) => ({
        status,
        getResponseHeader: (name) => (name === "Retry-After" && retryAfter !== undefined ? String(retryAfter) : null),
    });
    const refuse = (trigger, status, retryAfter) =>
        listeners["htmx:responseError"]({ detail: { elt: element(trigger), xhr: xhr(status, retryAfter) } });
    const request = (trigger) => {
        let prevented = false;
        listeners["htmx:beforeRequest"]({
            detail: { elt: element(trigger) },
            preventDefault: () => { prevented = true; },
        });
        return prevented;
    };
    return { refuse, request, dispatched };
}

test("a 429 on a poll parks every poll on the page", () => {
    const { refuse, request } = setup();
    assert.equal(request("every 30s"), false);
    refuse("every 30s", 429, 120);
    assert.equal(request("every 30s"), true);
    assert.equal(request("every 60s, verdict-update"), true);
});

test("user-initiated requests are never cancelled", () => {
    const { refuse, request } = setup();
    refuse("every 30s", 429, 120);
    // A click or a change: nobody would understand these silently doing nothing.
    assert.equal(request("click"), false);
    assert.equal(request("change"), false);
    assert.equal(request(null), false);
});

test("only a 429 pauses polling", () => {
    const { refuse, request } = setup();
    refuse("every 30s", 500, 120);
    refuse("every 30s", 404);
    assert.equal(request("every 30s"), false);
});

test("a 429 on a user-initiated request pauses nothing", () => {
    const { refuse, request } = setup();
    refuse("click", 429, 120);
    assert.equal(request("every 30s"), false);
});

test("the pause is announced with the clamped Retry-After", () => {
    const { refuse, dispatched } = setup();
    refuse("every 30s", 429, 90);
    assert.deepEqual(dispatched.map((e) => [e.type, e.detail.seconds]), [["noca:poll-backoff", 90]]);
});

test("a missing, absurd or hostile Retry-After still yields a sane pause", () => {
    for (const [header, expected] of [[undefined, 60], ["not-a-number", 60], ["0", 60], ["1", 5], ["99999", 600]]) {
        const { refuse, dispatched } = setup();
        refuse("every 30s", 429, header);
        assert.equal(dispatched[0].detail.seconds, expected, "Retry-After: " + header);
    }
});
