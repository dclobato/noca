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
 *   - time_all:      [count, ...]
 *   - time_accepted: [count, ...]
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

    // ── Helper: build a bar chart option ──────────────────────────────────
    function barOption(title, labels, values, color) {
        return {
            tooltip: {
                trigger: "axis",
                axisPointer: { type: "shadow" },
            },
            grid: {
                left: "3%",
                right: "4%",
                bottom: "3%",
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
                name: title,
                minInterval: 1,
            },
            series: [
                {
                    type: "bar",
                    data: values,
                    itemStyle: { color: color },
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

    // ── Bar charts ───────────────────────────────────────────────────────
    var runsTimeEl = document.getElementById("chart-runs-time");
    if (runsTimeEl && data.time_labels && data.time_all) {
        var runsBar = echarts.init(runsTimeEl);
        runsBar.setOption(
            barOption("Runs", data.time_labels, data.time_all, "#0d6efd")
        );
        charts.push(runsBar);
    }

    var acceptedTimeEl = document.getElementById("chart-accepted-time");
    if (acceptedTimeEl && data.time_labels && data.time_accepted) {
        var acceptedBar = echarts.init(acceptedTimeEl);
        var accLabel = data.accept_label || "AC";
        acceptedBar.setOption(
            barOption(
                accLabel + " Runs",
                data.time_labels,
                data.time_accepted,
                "#198754"
            )
        );
        charts.push(acceptedBar);
    }

    // ── Responsive resize ────────────────────────────────────────────────
    window.addEventListener("resize", function () {
        charts.forEach(function (c) {
            c.resize();
        });
    });
});
