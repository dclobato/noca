// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Initialises ECharts instances for the contest reports page.
 *
 * Expects a <script id="report-chart-data" type="application/json"> element
 * containing a JSON object with the following keys:
 *   - runs_pie:      [{value, name, color}, ...]
 *   - accepted_pie:  [{value, name, color}, ...]
 *   - time_labels:   ["0-10", "10-20", ...]
 *   - time_all:      [count, ...]   (total runs per window; cumulative is
 *                                   derived client-side)
 *   - time_accepted: [count, ...]   (accepted runs per window; cumulative
 *                                   is derived client-side)
 *   - accept_label:  "AC" or "AC + PE"
 */
document.addEventListener("DOMContentLoaded", function () {
    "use strict";

    function token(name) {
        return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    }

    // The AC/Non-AC/cumulative series colours are domain-specific (verdict
    // semantics), so they come from noca's own tokens rather than
    // NocaECharts's generic categorical palette. Read fresh on every call,
    // not memoized: the footer's theme toggle flips data-bs-theme without a
    // page reload, NocaECharts.create() then disposes and re-renders each
    // chart, and getComputedStyle must be re-queried at that moment to pick
    // up the new theme's values -- a value captured once at page load would
    // otherwise stay stale after the very first toggle (e.g. a line drawn in
    // the old theme's dark ink, now invisible against the new dark surface).
    function seriesColors() {
        return {
            accepted: token("--noca-success") || "#2f9e41",
            rejected: token("--noca-on-surface-variant") || "#64748b",
            // Cumulative AC previously reused the brand-container green,
            // which sits only ~1.35:1 from the AC bar's green (both
            // saturated, similar lightness) -- the line all but vanished
            // wherever it crossed the bars. Neither cumulative line is green
            // now, so both stay legible against the green/gray bars.
            cumAll: token("--noca-on-surface") || "#1e293b",
            cumAccepted: token("--noca-info") || "#0891b2",
        };
    }

    var raw = document.getElementById("report-chart-data");
    if (!raw) return;

    var data;
    try {
        data = JSON.parse(raw.textContent);
    } catch (_e) {
        return;
    }

    // Removes the "chart unavailable" placeholder immediately before a chart
    // successfully mounts, so it never sits behind (or shows through) the
    // canvas ECharts renders into.
    function clearPlaceholder(el) {
        el.textContent = "";
    }

    // ── Helper: build a pie chart option ──────────────────────────────────
    // Text (labels, legend, tooltip) comes from the noca-light/noca-dark
    // theme NocaECharts registers, so it stays legible and updates on theme
    // toggle without this file tracking it.
    function pieOption(title, items) {
        return {
            tooltip: {
                trigger: "item",
                formatter: "{b}: {c} ({d}%)",
            },
            series: [
                {
                    name: title,
                    type: "pie",
                    radius: ["30%", "65%"],
                    avoidLabelOverlap: true,
                    itemStyle: {
                        borderRadius: 4,
                        borderColor: "#fff",
                        borderWidth: 2,
                    },
                    label: {
                        formatter: "{b}\n{d}%",
                    },
                    data: items.map(function (item) {
                        return {
                            value: item.value,
                            name: item.name,
                            itemStyle: { color: item.color },
                        };
                    }),
                },
            ],
        };
    }

    // ── Helper: build a stacked bar chart option ──────────────────────────
    // Total bar height is the window's run volume, split into accepted and
    // non-accepted (rejected) segments. When `cumulativeAll` and/or
    // `cumulativeAccepted` are provided, line series are added on a
    // right-hand Y-axis showing cumulative counts through each window.
    // `colors` is a fresh seriesColors() snapshot -- see the mount() call
    // site, which rebuilds it on every render, including theme-toggle
    // re-renders. Axis/legend/tooltip text again comes from the registered
    // noca-light/noca-dark theme, not from this function.
    function stackedBarOption(labels, accepted, rejected, acceptLabel, cumulativeAll, cumulativeAccepted, colors) {
        var series = [
            {
                name: acceptLabel,
                type: "bar",
                stack: "runs",
                data: accepted,
                itemStyle: { color: colors.accepted },
            },
            {
                name: "Non-" + acceptLabel,
                type: "bar",
                stack: "runs",
                data: rejected,
                itemStyle: { color: colors.rejected },
            },
        ];
        var yAxis = [
            {
                type: "value",
                name: "Runs",
                minInterval: 1,
            },
        ];
        var hasOverlay = false;
        if (cumulativeAll && cumulativeAll.length > 0) {
            series.push({
                name: "Cumulative runs",
                type: "line",
                yAxisIndex: 1,
                smooth: true,
                showSymbol: false,
                lineStyle: { width: 2 },
                itemStyle: { color: colors.cumAll },
                data: cumulativeAll,
            });
            hasOverlay = true;
        }
        if (cumulativeAccepted && cumulativeAccepted.length > 0) {
            series.push({
                name: "Cumulative " + acceptLabel,
                type: "line",
                yAxisIndex: 1,
                smooth: true,
                showSymbol: false,
                lineStyle: { width: 2 },
                itemStyle: { color: colors.cumAccepted },
                data: cumulativeAccepted,
            });
            hasOverlay = true;
        }
        if (hasOverlay) {
            yAxis.push({
                type: "value",
                name: "Cumulative",
                position: "right",
                splitLine: { show: false },
                minInterval: 1,
            });
        }
        var legendData = [acceptLabel, "Non-" + acceptLabel];
        if (cumulativeAll && cumulativeAll.length > 0) {
            legendData.push("Cumulative runs");
        }
        if (cumulativeAccepted && cumulativeAccepted.length > 0) {
            legendData.push("Cumulative " + acceptLabel);
        }
        return {
            tooltip: {
                trigger: "axis",
                axisPointer: { type: "shadow" },
            },
            legend: {
                data: legendData,
                top: 0,
            },
            grid: {
                left: "3%",
                right: "6%",
                bottom: "3%",
                top: 40,
                containLabel: true,
            },
            xAxis: {
                type: "category",
                data: labels,
                axisLabel: { rotate: 45, fontSize: 10 },
                name: "Minutes",
                nameLocation: "center",
                nameGap: 35,
                barCategoryGap: "2%",
            },
            yAxis: yAxis,
            series: series,
        };
    }

    // Each chart mounts independently: a thrown error in one (a corrupt
    // option, an ECharts bug) must not stop the others from rendering, and
    // the div's placeholder text is the visible signal something went wrong.
    // NocaECharts.create() owns the chart instance from here on: it disposes
    // and re-invokes buildOption() itself whenever the footer toggle flips
    // data-bs-theme, so this file never has to listen for that separately.
    function mount(el, buildOption) {
        if (!el) return;
        try {
            clearPlaceholder(el);
            var mgr = NocaECharts.create(el);
            mgr.render(function (chart) {
                chart.setOption(buildOption());
            });
        } catch (_e) {
            /* NocaECharts.create()/setOption threw before the chart could
               mount: put the placeholder back so the failure stays visible. */
            el.textContent = "Chart unavailable. Reload the page to try again.";
        }
    }

    // ── Pie charts ───────────────────────────────────────────────────────
    var runsPieEl = document.getElementById("chart-runs-dist");
    if (data.runs_pie && data.runs_pie.length > 0) {
        mount(runsPieEl, function () {
            return pieOption("Runs", data.runs_pie);
        });
    }

    var acceptedPieEl = document.getElementById("chart-accepted-dist");
    var acceptLabel = data.accept_label || "AC";
    if (data.accepted_pie && data.accepted_pie.length > 0) {
        mount(acceptedPieEl, function () {
            return pieOption(acceptLabel + " Runs", data.accepted_pie);
        });
    }

    // ── Stacked bar chart (runs by time, AC vs non-AC) ────────────────────
    var runsTimeEl = document.getElementById("chart-runs-time");
    if (data.time_labels && data.time_all && data.time_accepted) {
        mount(runsTimeEl, function () {
            var rejected = data.time_all.map(function (total, i) {
                return Math.max(0, total - (data.time_accepted[i] || 0));
            });
            var runningAll = 0;
            var cumulativeAll = data.time_all.map(function (total) {
                runningAll += total;
                return runningAll;
            });
            var runningAc = 0;
            var cumulativeAccepted = data.time_accepted.map(function (count) {
                runningAc += count;
                return runningAc;
            });
            return stackedBarOption(
                data.time_labels,
                data.time_accepted,
                rejected,
                acceptLabel,
                cumulativeAll,
                cumulativeAccepted,
                seriesColors()
            );
        });
    }
});
