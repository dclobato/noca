//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { installDom, loadScript, makeElement } = require("./_dom_stub.js");

// A controllable EventSource: tests drive open/refuse/drop by hand.
function fakeEventSourceClass(log) {
    function FakeSource(url) {
        this.url = url;
        this.readyState = FakeSource.CONNECTING;
        this.closed = false;
        log.push(["connect", url]);
        FakeSource.instances.push(this);
    }
    FakeSource.CONNECTING = 0;
    FakeSource.OPEN = 1;
    FakeSource.CLOSED = 2;
    FakeSource.instances = [];
    FakeSource.prototype.close = function () { this.closed = true; };
    FakeSource.prototype.open = function () { this.readyState = FakeSource.OPEN; this.onopen && this.onopen(); };
    FakeSource.prototype.refuse = function () { this.readyState = FakeSource.CLOSED; this.onerror && this.onerror(); };
    FakeSource.prototype.drop = function () { this.readyState = FakeSource.CONNECTING; this.onerror && this.onerror(); };
    return FakeSource;
}

function setup() {
    const dom = installDom([]);
    const body = makeElement("body");
    body.appendChild = (child) => { child.parentNode = body; body.children.push(child); return child; };
    body.removeChild = (child) => { body.children = body.children.filter((c) => c !== child); };
    global.document.body = body;
    global.document.getElementById = (id) => body.children.find((c) => c.id === id) || null;
    const timers = [];
    const log = [];
    const Source = fakeEventSourceClass(log);
    global.window = {
        EventSource: Source,
        setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
        clearTimeout: (handle) => { timers[handle - 1] = null; },
    };
    const NocaSse = loadScript("shared/static/js/noca-sse.js");
    const banner = () => body.children.find((c) => c.id === NocaSse.BANNER_ID) || null;
    const fire = () => { const t = timers.shift(); if (t) t.fn(); };
    return { dom, log, Source, NocaSse, banner, timers, fire };
}

test("a transient error is left to the browser: no banner, no manual retry", () => {
    const { Source, NocaSse, banner, timers } = setup();
    const events = [];
    NocaSse.open("/events", { onMessage: (e) => events.push(e.data), onUnavailable: () => events.push("unavailable") });
    Source.instances[0].open();
    Source.instances[0].onmessage({ data: "refresh" });
    Source.instances[0].drop();
    assert.deepEqual(events, ["refresh"]);
    assert.equal(banner(), null);
    assert.equal(timers.length, 0);
    assert.equal(Source.instances.length, 1);
});

test("a refused connection shows the banner once and retries with capped backoff", () => {
    const { Source, NocaSse, banner, timers, fire } = setup();
    const calls = [];
    NocaSse.open("/events", { onMessage: () => {}, onUnavailable: () => calls.push("unavailable") });
    const delays = [];
    for (let i = 0; i < 7; i += 1) {
        Source.instances[i].refuse();
        delays.push(timers[0].ms);
        fire();
    }
    assert.deepEqual(calls, ["unavailable"], "onUnavailable fires once per outage");
    assert.deepEqual(delays, [5000, 10000, 20000, 40000, 60000, 60000, 60000]);
    assert.ok(banner(), "the banner is on the page");
    assert.match(banner().textContent, /Live updates are unavailable/);
    assert.equal(banner().getAttribute("role"), "status");
    assert.equal(Source.instances.length, 8, "each retry opened a fresh source");
});

test("recovery clears the banner, resets backoff, and notifies the consumer", () => {
    const { Source, NocaSse, banner, timers, fire } = setup();
    const calls = [];
    NocaSse.open("/events", { onMessage: () => {}, onOpen: () => calls.push("open"), onRecovered: () => calls.push("recovered") });
    Source.instances[0].refuse();
    fire();
    Source.instances[1].refuse();
    fire();
    Source.instances[2].open();
    assert.equal(banner(), null);
    assert.deepEqual(calls, ["recovered", "open"]);
    Source.instances[2].refuse();
    assert.equal(timers[0].ms, 5000, "backoff restarts after a successful open");
});

test("two refused streams share one banner until both recover", () => {
    const { Source, NocaSse, banner } = setup();
    NocaSse.open("/a", { onMessage: () => {} });
    NocaSse.open("/b", { onMessage: () => {} });
    Source.instances[0].refuse();
    Source.instances[1].refuse();
    assert.ok(banner());
    Source.instances[0].open();
    assert.ok(banner(), "still one stream down");
    Source.instances[1].open();
    assert.equal(banner(), null);
});

test("close() cancels the pending retry and releases the banner", () => {
    const { Source, NocaSse, banner, timers } = setup();
    const stream = NocaSse.open("/events", { onMessage: () => {} });
    Source.instances[0].refuse();
    stream.close();
    assert.equal(timers[0], null, "retry timer cleared");
    assert.equal(banner(), null);
    assert.ok(Source.instances[0].closed);
});
