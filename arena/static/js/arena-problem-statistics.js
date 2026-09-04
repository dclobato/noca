//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

// Fetches the precomputed statistics payload once and renders the judged
// distributions (doughnuts, per-language tables, wall-time histogram). The
// solver surfaces -- first/last solver, attempts histogram, submission heatmap
// -- live in ArenaProblemSolverStats and receive the same payload.
var ArenaProblemStatistics = (function () {
    var VERDICT_LABELS = {
        AC: "Accepted",
        PE: "Presentation Error",
        WA: "Wrong Answer",
        TLE: "Time Limit Exceeded",
        MLE: "Memory Limit Exceeded",
        OLE: "Output Limit Exceeded",
        RE: "Runtime Error",
        CE: "Compilation Error",
    };
    // Bootstrap-aligned verdict colors (green = AC, red = wrong, amber = limits).
    var VERDICT_COLORS = {
        AC: "#198754",
        PE: "#dc3545",
        WA: "#b02a37",
        TLE: "#ffc107",
        MLE: "#fd7e14",
        OLE: "#e8a13a",
        RE: "#6f42c1",
        CE: "#6c757d",
    };

    function _initChart(elementId) {
        var el = document.getElementById(elementId);
        if (!el) return null;
        return NocaECharts.create(el);
    }

    function _emptyOption(message) {
        return {
            graphic: [{
                type: "text",
                left: "center",
                top: "middle",
                style: { text: message, fontSize: 14, fill: NocaECharts.tokens().emptyText },
            }],
        };
    }

    function _doughnutOption(title, rows, labelFn, colorFn) {
        var data = rows.map(function (row) {
            var item = { value: row.count, name: labelFn(row) };
            var color = colorFn ? colorFn(row) : null;
            if (color) item.itemStyle = { color: color };
            return item;
        });
        return {
            aria: { enabled: true },
            tooltip: {
                trigger: "item",
                formatter: "{b}: {c} ({d}%)",
            },
            legend: { type: "plain", bottom: 0 },
            series: [{
                name: title,
                type: "pie",
                radius: ["28%", "72%"],
                center: ["50%", "42%"],
                avoidLabelOverlap: true,
                itemStyle: { borderColor: NocaECharts.tokens().sliceBorder, borderWidth: 1 },
                label: { show: false },
                data: data,
            }],
        };
    }

    function _histogramOption(payload) {
        var bins = payload.histogram_bins || 20;
        var timeLimit = payload.time_limit_ms || 0;
        var binWidth = timeLimit / bins;
        var categories = [];
        for (var i = 0; i < bins; i++) {
            categories.push(Math.round(binWidth * i) + "–" + Math.round(binWidth * (i + 1)));
        }
        var series = (payload.wall_time_histogram || []).map(function (lang) {
            return {
                name: lang.name,
                type: "bar",
                stack: "total",
                barCategoryGap: "0%",
                emphasis: { focus: "series" },
                data: lang.counts,
            };
        });
        return {
            aria: { enabled: true },
            tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
            legend: { type: "plain", bottom: 0 },
            grid: { left: 50, right: 20, top: 20, bottom: 90 },
            xAxis: {
                type: "category",
                data: categories,
                name: "Wall time (ms)",
                nameLocation: "middle",
                nameGap: 32,
                axisLabel: { interval: Math.floor(bins / 10), rotate: 0 },
            },
            yAxis: { type: "value", name: "Solutions", minInterval: 1 },
            series: series,
        };
    }

    function _messageRow(tbodySelector, message, className) {
        var tbody = document.querySelector(tbodySelector);
        if (!tbody) return;
        tbody.innerHTML = "";
        var tr = document.createElement("tr");
        var cell = document.createElement("td");
        cell.colSpan = 4;
        cell.className = className;
        cell.textContent = message;
        tr.appendChild(cell);
        tbody.appendChild(tr);
    }

    function _fillTable(tbodySelector, rows, avgKey, stddevKey) {
        if (!rows || rows.length === 0) {
            _messageRow(tbodySelector, "No accepted solutions yet.", "text-muted text-center");
            return;
        }
        var tbody = document.querySelector(tbodySelector);
        if (!tbody) return;
        tbody.innerHTML = "";
        rows.forEach(function (row) {
            var tr = document.createElement("tr");
            tr.appendChild(_td(row.name, ""));
            tr.appendChild(_td(String(row.count), "text-end"));
            tr.appendChild(_td(Number(row[avgKey]).toFixed(1), "text-end"));
            tr.appendChild(_td(Number(row[stddevKey]).toFixed(1), "text-end"));
            tbody.appendChild(tr);
        });
    }

    function _td(text, className) {
        var td = document.createElement("td");
        if (className) td.className = className;
        td.textContent = text;
        return td;
    }

    function _render(payload, verdictChart, languageChart, histogramChart, solverStats) {
        var hasData = payload && payload.total_submissions > 0;

        var computedEl = document.querySelector("[data-stats-computed-at]");
        if (computedEl && payload && payload.computed_at) {
            computedEl.textContent =
                "Updated " + (payload.computed_at_display || new Date(payload.computed_at).toLocaleString());
        }

        if (verdictChart) verdictChart.hideLoading();
        if (languageChart) languageChart.hideLoading();
        if (histogramChart) histogramChart.hideLoading();
        // Solver surfaces judge their own emptiness: a pending-only problem has
        // no judged distribution but still has a heatmap.
        if (solverStats) solverStats.render(payload || {});

        if (!hasData) {
            if (verdictChart) verdictChart.render(function (chart) { chart.setOption(_emptyOption("No submissions yet."), true); });
            if (languageChart) languageChart.render(function (chart) { chart.setOption(_emptyOption("No submissions yet."), true); });
            if (histogramChart) histogramChart.render(function (chart) { chart.setOption(_emptyOption("No accepted solutions yet."), true); });
            _fillTable("[data-stats-time-table]", [], "avg_ms", "stddev_ms");
            _fillTable("[data-stats-memory-table]", [], "avg_kb", "stddev_kb");
            return;
        }

        if (verdictChart) {
            verdictChart.render(function (chart) {
                chart.setOption(_doughnutOption(
                    "Verdicts",
                    payload.verdicts,
                    function (row) { return VERDICT_LABELS[row.verdict] || row.verdict; },
                    function (row) { return VERDICT_COLORS[row.verdict] || null; }
                ));
            });
        }
        if (languageChart) {
            languageChart.render(function (chart) {
                chart.setOption(_doughnutOption(
                    "Languages",
                    payload.languages,
                    function (row) { return row.name; },
                    null
                ));
            });
        }
        if (histogramChart) {
            if (payload.wall_time_histogram && payload.wall_time_histogram.length > 0) {
                histogramChart.render(function (chart) { chart.setOption(_histogramOption(payload)); });
            } else {
                histogramChart.render(function (chart) { chart.setOption(_emptyOption("No accepted solutions yet."), true); });
            }
        }

        _fillTable("[data-stats-time-table]", payload.time_stats, "avg_ms", "stddev_ms");
        _fillTable("[data-stats-memory-table]", payload.memory_stats, "avg_kb", "stddev_kb");
    }

    function _renderError(verdictChart, languageChart, histogramChart, solverStats) {
        var message = "Failed to load statistics.";
        if (solverStats) solverStats.renderError(message);
        [verdictChart, languageChart, histogramChart].forEach(function (mgr) {
            if (!mgr) return;
            mgr.hideLoading();
            mgr.render(function (chart) { chart.setOption(_emptyOption(message), true); });
        });
        _messageRow("[data-stats-time-table]", message, "text-danger text-center");
        _messageRow("[data-stats-memory-table]", message, "text-danger text-center");
    }

    function init() {
        if (typeof echarts === "undefined") {
            console.error("ArenaProblemStatistics: echarts is not loaded.");
            return;
        }
        var root = document.querySelector("[data-arena-problem-stats]");
        if (!root) return;
        var url = root.dataset.statsUrl;

        var verdictChart = _initChart("problem-stats-verdicts");
        var languageChart = _initChart("problem-stats-languages");
        var histogramChart = _initChart("problem-stats-histogram");
        var solverStats = typeof ArenaProblemSolverStats !== "undefined" ? ArenaProblemSolverStats.init() : null;
        [verdictChart, languageChart, histogramChart].forEach(function (mgr) {
            if (mgr) mgr.showLoading();
        });
        _messageRow("[data-stats-time-table]", "Loading…", "text-muted text-center");
        _messageRow("[data-stats-memory-table]", "Loading…", "text-muted text-center");

        fetch(url)
            .then(function (response) {
                if (!response.ok) throw new Error("HTTP " + response.status);
                return response.json();
            })
            .then(function (payload) {
                _render(payload || {}, verdictChart, languageChart, histogramChart, solverStats);
            })
            .catch(function (err) {
                console.error("ArenaProblemStatistics: failed to load data.", err);
                _renderError(verdictChart, languageChart, histogramChart, solverStats);
            });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

    return { init: init, emptyOption: _emptyOption };
}());
