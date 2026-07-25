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
 *   - time_all:      [count, ...]   (total runs per window)
 *   - time_accepted: [count, ...]   (accepted runs per window)
 *   - accept_label:  "AC" or "AC + PE"
 */
document.addEventListener("DOMContentLoaded", function () {
    "use strict";

    var raw = document.getElementById("report-chart-data");
    if (!raw) return;

    var data;
    try {
        data = JSON.parse(raw.textContent);
    } catch (_e) {
        return;
    }

    var charts = [];

    // ── Helper: build a pie chart option ──────────────────────────────────
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
    // non-accepted (rejected) segments.
    function stackedBarOption(labels, accepted, rejected, acceptLabel) {
        return {
            tooltip: {
                trigger: "axis",
                axisPointer: { type: "shadow" },
            },
            legend: {
                data: [acceptLabel, "Non-" + acceptLabel],
                top: 0,
            },
            grid: {
                left: "3%",
                right: "4%",
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
            },
            yAxis: {
                type: "value",
                name: "Runs",
                minInterval: 1,
            },
            series: [
                {
                    name: acceptLabel,
                    type: "bar",
                    stack: "runs",
                    data: accepted,
                    itemStyle: { color: "#198754" },
                    barMaxWidth: 40,
                },
                {
                    name: "Non-" + acceptLabel,
                    type: "bar",
                    stack: "runs",
                    data: rejected,
                    itemStyle: { color: "#adb5bd" },
                    barMaxWidth: 40,
                },
            ],
        };
    }

    // ── Pie charts ───────────────────────────────────────────────────────
    var runsPieEl = document.getElementById("chart-runs-dist");
    if (runsPieEl && data.runs_pie && data.runs_pie.length > 0) {
        var runsPie = echarts.init(runsPieEl);
        runsPie.setOption(pieOption("Runs", data.runs_pie));
        charts.push(runsPie);
    }

    var acceptedPieEl = document.getElementById("chart-accepted-dist");
    if (acceptedPieEl && data.accepted_pie && data.accepted_pie.length > 0) {
        var acceptedPie = echarts.init(acceptedPieEl);
        var acceptLabel = data.accept_label || "AC";
        acceptedPie.setOption(pieOption(acceptLabel + " Runs", data.accepted_pie));
        charts.push(acceptedPie);
    }

    // ── Stacked bar chart (runs by time, AC vs non-AC) ────────────────────
    var runsTimeEl = document.getElementById("chart-runs-time");
    if (runsTimeEl && data.time_labels && data.time_all && data.time_accepted) {
        var accLabel = data.accept_label || "AC";
        var rejected = data.time_all.map(function (total, i) {
            return Math.max(0, total - (data.time_accepted[i] || 0));
        });
        var runsBar = echarts.init(runsTimeEl);
        runsBar.setOption(
            stackedBarOption(
                data.time_labels,
                data.time_accepted,
                rejected,
                accLabel
            )
        );
        charts.push(runsBar);
    }

    // ── Responsive resize ────────────────────────────────────────────────
    window.addEventListener("resize", function () {
        charts.forEach(function (c) {
            c.resize();
        });
    });
});
