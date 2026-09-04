//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { installDom, fakeChartManager, loadScript } = require("./_dom_stub.js");

function setup(ids) {
    const dom = installDom(ids);
    const log = [];
    global.echarts = {};
    global.NocaECharts = { create: () => fakeChartManager(log), tokens: () => ({ emptyText: "#000" }) };
    global.console = { ...console, error: () => {} };
    return { dom, log };
}

function lastSetOption(log) {
    return log.filter((e) => Array.isArray(e) && e[0] === "setOption").pop()[1];
}

test("render() draws an already-fetched payload without any fetch", () => {
    const { dom, log } = setup(["chart"]);
    let fetched = 0;
    global.fetch = () => { fetched++; return Promise.reject(new Error("no")); };
    const Heatmap = loadScript("arena/static/js/arena-submission-heatmap.js");

    Heatmap.render("chart", { heatmap: [["2026-03-03", 4]], range_start: "2026-01-01", range_end: "2026-12-31" });

    assert.equal(fetched, 0);
    const option = lastSetOption(log);
    assert.deepEqual(option.calendar.range, ["2026-01-01", "2026-12-31"]);
    assert.deepEqual(option.series.data, [["2026-03-03", 4]]);
    assert.equal(option.aria.enabled, true);
    assert.equal(option.tooltip.formatter({ value: ["2026-03-03", 1] }), "2026-03-03<br/><strong>1 submission</strong>");
    assert.match(dom.byId.chart.style.width, /px$/);
});

test("render() honours formatDate in the tooltip and emptyMessage on an empty payload", () => {
    const { log } = setup(["chart"]);
    const Heatmap = loadScript("arena/static/js/arena-submission-heatmap.js");

    Heatmap.render("chart", { heatmap: [["2024-02-29", 2]], range_start: "2024-01-01", range_end: "2024-12-31" },
        { formatDate: (iso) => "day " + iso.slice(8) });
    assert.equal(lastSetOption(log).tooltip.formatter({ value: ["2024-02-29", 2] }), "day 29<br/><strong>2 submissions</strong>");

    Heatmap.render("chart", { heatmap: [] }, { emptyMessage: "Nothing here." });
    assert.equal(lastSetOption(log).graphic[0].style.text, "Nothing here.");
});

test("init() keeps fetching its URL and auto-binds [data-arena-submission-heatmap]", async () => {
    const { dom, log } = setup(["profile-heatmap"]);
    dom.byId["profile-heatmap"].dataset.heatmapUrl = "/profile/u1/submission-heatmap.json";
    const requested = [];
    global.fetch = (url) => {
        requested.push(url);
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ heatmap: [["2026-06-22", 3]], range_start: "2025-06-23", range_end: "2026-06-22" }) });
    };

    loadScript("arena/static/js/arena-submission-heatmap.js");
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.deepEqual(requested, ["/profile/u1/submission-heatmap.json"]);
    assert.deepEqual(lastSetOption(log).series.data, [["2026-06-22", 3]]);
    assert.ok(log.includes("showLoading") && log.includes("hideLoading"));
});
