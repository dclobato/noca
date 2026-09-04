//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { installDom, fakeChartManager, loadScript } = require("./_dom_stub.js");

function setup() {
    const dom = installDom(["problem-stats-attempts", "problem-stats-heatmap"]);
    const log = [];
    const heatmapCalls = [];
    global.NocaECharts = { create: () => fakeChartManager(log), tokens: () => ({ emptyText: "#000" }) };
    global.ArenaProblemStatistics = { emptyOption: (message) => ({ empty: message }) };
    global.ArenaSubmissionHeatmap = { render: (id, payload, options) => heatmapCalls.push({ id, payload, options: options || {} }) };
    const Stats = loadScript("arena/static/js/arena-problem-solver-stats.js");
    const buttons = () => dom.created.filter((el) => el.tag === "button");
    return { dom, log, heatmapCalls, Stats, buttons, text: (sel) => dom.query(sel).textContent };
}

const DAYS = [["2024-02-29", 2], ["2026-03-03", 4], ["2026-12-31", 1]];
const PAYLOAD = {
    solver_count: 3,
    median_attempts: 2.5,
    first_solver: { user_id: "u1", name: "Ada", solved_at: "2026-01-01T00:00:00+00:00", solved_at_display: "2026-01-01 00:00 UTC", profile_url: "/profile/u1" },
    last_solver: { user_id: "u2", name: "Bob", solved_at: "2026-02-01T00:00:00+00:00", solved_at_display: "2026-02-01 00:00 UTC" },
    attempts_histogram: [{ label: "1", min: 1, max: 1, count: 1 }, { label: "6-10", min: 6, max: 10, count: 2 }],
    submission_heatmap: { first_date: "2024-02-29", last_date: "2026-12-31", days: DAYS },
};

test("solver facts link only solvers carrying profile_url and show display timestamps", () => {
    const { dom, Stats, text } = setup();
    Stats.init().render(PAYLOAD);

    const first = dom.query("[data-stats-first-solver]").children;
    assert.equal(first[0].tag, "a");
    assert.equal(first[0].href, "/profile/u1");
    assert.equal(first[1].textContent, "2026-01-01 00:00 UTC");
    assert.equal(dom.query("[data-stats-last-solver]").children[0].tag, "span");
});

test("attempts chart categories come from the payload labels", () => {
    const { log, Stats, text } = setup();
    Stats.init().render(PAYLOAD);

    const option = log.filter((e) => Array.isArray(e) && e[0] === "setOption").pop()[1];
    assert.deepEqual(option.xAxis.data, ["1", "6-10"]);
    assert.deepEqual(option.series[0].data, [1, 2]);
    assert.equal(option.aria.enabled, true);
    assert.equal(text("[data-stats-attempts-status]"), "3 solvers. Median 2.5 attempts.");
});

// arena_problem_solvers is authoritative; a solver row carrying no AC judgment
// at all has no attempt count, so the two numbers can still disagree.
test("the footnote reports the solver total, not the smaller identifiable subset", () => {
    const { log, Stats, text } = setup();
    const handle = Stats.init();

    handle.render(Object.assign({}, PAYLOAD, { solver_count: 6, median_attempts: 1 }));
    assert.equal(
        text("[data-stats-attempts-status]"),
        "6 solvers. Median 1.0 attempts, over the 3 with a known first accepted submission."
    );

    handle.render({ solver_count: 1, attempts_histogram: [], median_attempts: null });
    assert.equal(
        text("[data-stats-attempts-status]"),
        "1 solver. Attempt count unknown."
    );
    assert.deepEqual(
        log.filter((e) => Array.isArray(e) && e[0] === "setOption").pop()[1],
        { empty: "No attempt counts available." }
    );
});

test("year selector spans every year in range, defaults to the latest, and folds All years", () => {
    const { Stats, heatmapCalls, buttons, text } = setup();
    Stats.init().render(PAYLOAD);

    assert.deepEqual(buttons().map((b) => b.textContent), ["All years", "2026", "2025", "2024"]);
    assert.equal(buttons()[1].getAttribute("aria-pressed"), "true");
    let view = heatmapCalls.at(-1).payload;
    assert.deepEqual([view.range_start, view.range_end], ["2026-01-01", "2026-12-31"]);
    assert.deepEqual(view.heatmap, [["2026-03-03", 4], ["2026-12-31", 1]]);
    assert.equal(text("[data-stats-heatmap-status]"), "5 submissions on 2 days in 2026.");

    buttons()[2].click(); // 2025: a gap year renders an empty full calendar
    view = heatmapCalls.at(-1).payload;
    assert.deepEqual([view.range_start, view.range_end, view.heatmap], ["2025-01-01", "2025-12-31", []]);
    assert.equal(text("[data-stats-heatmap-status]"), "No submissions in 2025.");
    assert.equal(heatmapCalls.at(-1).options.emptyMessage, "No submissions in this period.");

    buttons()[0].click(); // All years
    view = heatmapCalls.at(-1).payload;
    assert.deepEqual([view.range_start, view.range_end], ["2024-01-01", "2024-12-31"]);
    assert.deepEqual(view.heatmap, [["2024-02-29", 2], ["2024-03-03", 4], ["2024-12-31", 1]]);
    assert.equal(heatmapCalls.at(-1).options.formatDate("2024-02-29"), "Feb 29");
    assert.equal(text("[data-stats-heatmap-status]"), "7 submissions on 3 days across all years, by day of the year.");
    assert.equal(buttons()[0].getAttribute("aria-pressed"), "true");
    assert.equal(buttons()[1].getAttribute("aria-pressed"), "false");
});

test("an empty payload and an error both render stated DOM states with no chart frames", () => {
    const { dom, log, Stats, heatmapCalls, text } = setup();
    const handle = Stats.init();

    handle.render({});
    assert.equal(text("[data-stats-first-solver]"), "No solvers yet.");
    assert.equal(text("[data-stats-attempts-status]"), "No solvers yet.");
    assert.equal(text("[data-stats-heatmap-status]"), "No submissions yet.");
    assert.equal(dom.query("[data-stats-heatmap-years]").hidden, true);
    assert.deepEqual(heatmapCalls.at(-1).payload, { heatmap: [] });
    assert.deepEqual(log.filter((e) => Array.isArray(e) && e[0] === "setOption").pop()[1], { empty: "No solvers yet." });

    handle.renderError("Failed to load statistics.");
    assert.equal(text("[data-stats-attempts-status]"), "Failed to load statistics.");
    assert.equal(text("[data-stats-heatmap-status]"), "Failed to load statistics.");
    assert.equal(heatmapCalls.at(-1).options.emptyMessage, "Failed to load statistics.");
});
