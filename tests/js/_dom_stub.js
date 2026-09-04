//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

// A deliberately tiny DOM stand-in for contract tests of the Arena page
// scripts: enough of document/element for querySelector-driven rendering,
// no layout, no events beyond click listeners.
const fs = require("node:fs");
const path = require("node:path");

const REPO_ROOT = path.resolve(__dirname, "..", "..");

function makeElement(tag) {
    const el = {
        tag,
        id: "",
        hidden: false,
        href: "",
        className: "",
        style: {},
        dataset: {},
        attributes: {},
        children: [],
        classes: new Set(),
        listeners: {},
    };
    el.classList = {
        add: (c) => el.classes.add(c),
        remove: (c) => el.classes.delete(c),
        contains: (c) => el.classes.has(c),
    };
    el.setAttribute = (name, value) => { el.attributes[name] = value; };
    el.getAttribute = (name) => (name in el.attributes ? el.attributes[name] : null);
    el.appendChild = (child) => { el.children.push(child); return child; };
    el.addEventListener = (name, fn) => { el.listeners[name] = fn; };
    el.click = () => el.listeners.click && el.listeners.click();
    // Assigning text (or empty HTML) replaces every child, as in a real DOM.
    let text = "";
    Object.defineProperty(el, "textContent", {
        set(value) { text = String(value); el.children = []; },
        get() { return text; },
    });
    Object.defineProperty(el, "innerHTML", {
        set(value) { if (value === "") el.children = []; },
        get() { return ""; },
    });
    return el;
}

function installDom(ids) {
    const byId = {};
    const bySelector = {};
    const created = [];
    (ids || []).forEach((id) => { byId[id] = makeElement("div"); byId[id].id = id; });
    global.document = {
        documentElement: { getAttribute: () => null },
        readyState: "complete",
        addEventListener: () => {},
        getElementById: (id) => byId[id] || null,
        querySelector: (sel) => {
            if (sel.startsWith("#")) return byId[sel.slice(1)] || null;
            if (!(sel in bySelector)) bySelector[sel] = makeElement("div");
            return bySelector[sel];
        },
        querySelectorAll: (sel) => (sel === "[data-arena-submission-heatmap]" ? Object.values(byId).filter((e) => "heatmapUrl" in e.dataset) : []),
        createElement: (tag) => { const el = makeElement(tag); created.push(el); return el; },
    };
    return { byId, bySelector, created, query: (sel) => global.document.querySelector(sel) };
}

function fakeChartManager(log) {
    return {
        showLoading: () => log.push("showLoading"),
        hideLoading: () => log.push("hideLoading"),
        resize: (size) => log.push(["resize", size]),
        dispose: () => log.push("dispose"),
        render: (fn) => fn({ setOption: (opt, notMerge) => log.push(["setOption", opt, notMerge]) }),
    };
}

function loadScript(relPath, globals) {
    Object.assign(global, globals || {});
    const src = fs.readFileSync(path.join(REPO_ROOT, relPath), "utf8");
    // The page scripts are classic IIFE globals ("var X = ..."); evaluate them
    // in the global scope so the assignment lands on `global`.
    return new Function(src + "\nreturn " + src.match(/var (\w+)/)[1] + ";")();
}

module.exports = { installDom, fakeChartManager, loadScript, makeElement };
