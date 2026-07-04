//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

var ArenaUserStatistics = (function () {
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

    var _instances = [];

    function _initChart(elementId) {
        var el = document.getElementById(elementId);
        if (!el) return null;
        var chart = echarts.init(el);
        _instances.push(chart);
        return chart;
    }

    function _emptyOption(message) {
        return {
            graphic: [{
                type: "text",
                left: "center",
                top: "middle",
                style: { text: message, fontSize: 14, fill: "#999" },
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
                itemStyle: { borderColor: "#fff", borderWidth: 1 },
                label: { show: false },
                data: data,
            }],
        };
    }

    function _render(payload) {
        var hasData = payload && payload.total_submissions > 0;

        var computedEl = document.querySelector("[data-stats-computed-at]");
        if (computedEl && payload && payload.computed_at) {
            var computed = new Date(payload.computed_at);
            computedEl.textContent = "Updated " + computed.toLocaleString();
        }

        var verdictChart = _initChart("public-user-stats-verdicts");
        var languageChart = _initChart("public-user-stats-languages");

        if (!hasData) {
            if (verdictChart) verdictChart.setOption(_emptyOption("No submissions yet."));
            if (languageChart) languageChart.setOption(_emptyOption("No submissions yet."));
            return;
        }

        if (verdictChart) {
            verdictChart.setOption(_doughnutOption(
                "Verdicts",
                payload.verdicts || [],
                function (row) { return VERDICT_LABELS[row.verdict] || row.verdict; },
                function (row) { return VERDICT_COLORS[row.verdict] || null; }
            ));
        }
        if (languageChart) {
            languageChart.setOption(_doughnutOption(
                "Languages",
                payload.languages || [],
                function (row) { return row.name; },
                null
            ));
        }
    }

    function init() {
        if (typeof echarts === "undefined") {
            console.error("ArenaUserStatistics: echarts is not loaded.");
            return;
        }
        var root = document.querySelector("[data-arena-user-stats]");
        if (!root) return;
        var url = root.dataset.statsUrl;

        fetch(url)
            .then(function (response) {
                if (!response.ok) throw new Error("HTTP " + response.status);
                return response.json();
            })
            .then(function (payload) {
                _render(payload || {});
            })
            .catch(function (err) {
                console.error("ArenaUserStatistics: failed to load data.", err);
                _render({});
            });

        window.addEventListener("resize", function () {
            _instances.forEach(function (chart) { chart.resize(); });
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

    return { init: init };
}());