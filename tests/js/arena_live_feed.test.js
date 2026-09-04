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
const vm = require("node:vm");

const REPO_ROOT = path.resolve(__dirname, "../..");

function loadRenderer() {
    let options = null;
    const liveFeed = {
        init: (value) => {
            options = value;
        },
    };
    const context = {
        window: { NocaLiveFeed: liveFeed },
        NocaLiveFeed: liveFeed,
    };
    const source = fs.readFileSync(
        path.join(REPO_ROOT, "arena/static/js/live-feed.js"),
        "utf8",
    );
    vm.runInNewContext(source, context);
    assert.ok(options);
    return options.renderRow;
}

const helpers = {
    escapeHtml: (value) => String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;"),
    formatUtc: (value) => value,
};

function completeRow() {
    return {
        created_at: "2026-09-01T12:00:00+00:00",
        created_at_display: "2026-09-01 09:00:00 -03",
        affiliation_name: "Universidade de São Paulo",
        affiliation_logo_url: "/affiliations/usp/logo",
        country_code: "BR",
        country_name: "Brazil",
        country_flag_url: "/static/vendor/img/state-flags/BR.svg",
        subdivision_code: "BR-SP",
        subdivision_name: "São Paulo",
        state_flag_url: "/static/vendor/img/state-flags/SP.svg",
        problem_number: 42,
        problem_title: "Distinct Values",
        problem_url: "/problems/42",
        language_name: "Python",
        language_icon_svg_url: "/static/vendor/img/devicon/python-original.svg",
        verdict_label: "Accepted",
        verdict_badge_class: "bg-success",
    };
}

test("renderer separates affiliation from Brazilian country and state origin", () => {
    const html = loadRenderer()(completeRow(), helpers);

    assert.equal((html.match(/<td/g) || []).length, 6);
    assert.match(html, /arena-live-feed-col-affiliation/);
    assert.match(html, /Universidade de São Paulo/);
    assert.match(html, /arena-live-feed-col-origin/);
    assert.match(html, /img\/state-flags\/BR\.svg/);
    assert.match(html, /arena-country-flag--circular/);
    assert.match(html, /img\/state-flags\/SP\.svg/);
    assert.ok(
        html.indexOf("Universidade de São Paulo") <
            html.indexOf("img/state-flags/BR.svg"),
    );
});

test("renderer keeps independent placeholders for missing affiliation and origin", () => {
    const row = completeRow();
    row.affiliation_name = null;
    row.affiliation_logo_url = null;
    row.country_flag_url = null;
    row.state_flag_url = null;

    const html = loadRenderer()(row, helpers);

    assert.match(html, /aria-label="No affiliation">—<\/span>/);
    assert.match(html, /aria-label="No origin">—<\/span>/);
    assert.doesNotMatch(html, /affiliation-logo-thumb/);
    assert.doesNotMatch(html, /arena-live-feed-state-flag/);
});
