//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

/**
 * Shared theming for every Arena ECharts chart.
 *
 * ECharts colours are set in JS, not CSS, so charts cannot follow the page's
 * data-bs-theme on their own. This helper registers `noca-light` / `noca-dark`
 * ECharts themes (axes, legend, tooltip, text, dataZoom, categorical palette)
 * and hands out a managed wrapper via `NocaECharts.create(el)`:
 *
 *   var mgr = NocaECharts.create(el);
 *   mgr.showLoading();
 *   fetch(url).then(...).then(function (payload) {
 *     mgr.hideLoading();
 *     mgr.render(function (chart) { chart.setOption(buildOption(payload)); });
 *   });
 *
 * The wrapper inits the chart with the active theme, re-runs the stored render
 * callback whenever the footer toggle flips data-bs-theme (re-reading
 * `NocaECharts.tokens()` so any per-series colour also updates), and owns the
 * window-resize handler so callers never hold a stale (disposed) instance.
 */
var NocaECharts = (function () {
    var LIGHT = {
        ink: "#1e293b",
        label: "#57606a",
        axis: "#cbd5e1",
        split: "#e6eaef",
        tooltipBg: "#ffffff",
        tooltipBorder: "#d0d7de",
        tooltipText: "#1e293b",
        sliceBorder: "#ffffff",
        emptyText: "#8a94a6",
        shadow: "rgba(100,116,139,0.12)",
        zoomFill: "rgba(47,158,65,0.10)",
        zoomBg: "#eef2f6",
        palette: ["#2f9e41", "#2563eb", "#d97706", "#a42f59", "#0891b2", "#7c3aed", "#dc3545", "#57606a"],
    };
    var DARK = {
        ink: "#e2e8f0",
        label: "#94a3b8",
        axis: "#475569",
        split: "#273449",
        tooltipBg: "#1e293b",
        tooltipBorder: "#334155",
        tooltipText: "#e2e8f0",
        sliceBorder: "#0f172a",
        emptyText: "#94a3b8",
        shadow: "rgba(148,163,184,0.16)",
        zoomFill: "rgba(57,211,83,0.14)",
        zoomBg: "#1e293b",
        palette: ["#39d353", "#58a6ff", "#e3b341", "#db61a2", "#39c5cf", "#bc8cff", "#f85149", "#94a3b8"],
    };

    var _registered = false;
    var _registry = [];
    var _observer = null;

    function _isDark() {
        return document.documentElement.getAttribute("data-bs-theme") === "dark";
    }

    function tokens() {
        return _isDark() ? DARK : LIGHT;
    }

    function themeName() {
        return _isDark() ? "noca-dark" : "noca-light";
    }

    function _axis(t) {
        return {
            axisLine: { show: true, lineStyle: { color: t.axis } },
            axisTick: { show: true, lineStyle: { color: t.axis } },
            axisLabel: { color: t.label },
            nameTextStyle: { color: t.label },
            splitLine: { lineStyle: { color: [t.split] } },
        };
    }

    function _themeObject(t) {
        var axis = _axis(t);
        return {
            color: t.palette,
            backgroundColor: "transparent",
            textStyle: { color: t.ink },
            title: { textStyle: { color: t.ink }, subtextStyle: { color: t.label } },
            categoryAxis: axis,
            valueAxis: axis,
            logAxis: axis,
            timeAxis: axis,
            legend: { textStyle: { color: t.ink } },
            visualMap: { textStyle: { color: t.ink } },
            tooltip: {
                backgroundColor: t.tooltipBg,
                borderColor: t.tooltipBorder,
                textStyle: { color: t.tooltipText },
                axisPointer: {
                    lineStyle: { color: t.axis },
                    crossStyle: { color: t.axis },
                    shadowStyle: { color: t.shadow },
                },
            },
            dataZoom: {
                textStyle: { color: t.label },
                borderColor: t.axis,
                fillerColor: t.zoomFill,
                dataBackground: { lineStyle: { color: t.axis }, areaStyle: { color: t.zoomBg } },
                handleStyle: { color: t.tooltipBg, borderColor: t.axis },
                moveHandleStyle: { color: t.axis },
            },
        };
    }

    function _ensureRegistered() {
        if (_registered || typeof echarts === "undefined") return;
        echarts.registerTheme("noca-light", _themeObject(LIGHT));
        echarts.registerTheme("noca-dark", _themeObject(DARK));
        _registered = true;
    }

    function _ensureObserver() {
        if (_observer) return;
        _observer = new MutationObserver(function () {
            _registry.forEach(function (entry) { entry.retheme(); });
        });
        _observer.observe(document.documentElement, {
            attributes: true,
            attributeFilter: ["data-bs-theme"],
        });
    }

    function create(el) {
        _ensureRegistered();
        _ensureObserver();

        var inst = echarts.init(el, themeName());
        var renderFn = null;
        var loading = false;

        function retheme() {
            var opts = { width: inst.getWidth(), height: inst.getHeight() };
            inst.dispose();
            inst = echarts.init(el, themeName());
            if (opts.width && opts.height) inst.resize(opts);
            if (loading) inst.showLoading();
            if (renderFn) renderFn(inst);
        }

        function resize() {
            if (!inst.isDisposed()) inst.resize();
        }

        var mgr = {
            render: function (fn) {
                renderFn = fn;
                fn(inst);
                return mgr;
            },
            showLoading: function () { loading = true; inst.showLoading(); return mgr; },
            hideLoading: function () { loading = false; inst.hideLoading(); return mgr; },
            resize: function (opts) { inst.resize(opts); return mgr; },
            chart: function () { return inst; },
            dispose: function () {
                _registry = _registry.filter(function (e) { return e !== entry; });
                window.removeEventListener("resize", resize);
                inst.dispose();
            },
        };

        var entry = { retheme: retheme };
        _registry.push(entry);

        window.addEventListener("resize", resize);

        return mgr;
    }

    return { create: create, tokens: tokens, themeName: themeName };
}());
