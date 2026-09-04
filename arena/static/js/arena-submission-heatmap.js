//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

// Annual calendar heatmap over NocaECharts. Two entry points share one render
// path: init(containerId, dataUrl) fetches a {heatmap, range_start, range_end}
// payload (profile pages, auto-bound through [data-arena-submission-heatmap]),
// and render(containerId, payload, options) draws a payload the page already
// holds (the problem statistics page). options.formatDate overrides the date
// label in the tooltip; options.emptyMessage overrides the empty-state text.
var ArenaSubmissionHeatmap = (function () {
    var _instances = {};
    var CELL_SIZE = 13;
    var LEFT_MARGIN = 30;
    var RIGHT_MARGIN = 10;
    var TOP_MARGIN = 20;
    var DAY_ROWS = 7;

    // The calendar sets every colour in JS, so it cannot follow data-bs-theme via
    // CSS. Pick a GitHub-style palette per theme; the shared managed wrapper
    // re-runs the render callback on toggle, so these are re-read automatically.
    function _palette() {
        var dark = document.documentElement.getAttribute("data-bs-theme") === "dark";
        if (dark) {
            return {
                label: "#8b949e",
                emptyCell: "#161b22",
                cellBorder: "#0d1117",
                ramp: ["#0e4429", "#006d32", "#26a641", "#39d353"],
                tooltipBg: "#1e293b",
                tooltipBorder: "#334155",
                tooltipText: "#e2e8f0",
            };
        }
        return {
            label: "#57606a",
            emptyCell: "#ebedf0",
            cellBorder: "#ffffff",
            ramp: ["#9be9a8", "#40c463", "#30a14e", "#216e39"],
            tooltipBg: "#ffffff",
            tooltipBorder: "#d0d7de",
            tooltipText: "#1e293b",
        };
    }

    function _dispose(containerId) {
        if (_instances[containerId]) {
            _instances[containerId].dispose();
            delete _instances[containerId];
        }
    }

    function _computeContainerWidth(rangeStart, rangeEnd) {
        var start = new Date(rangeStart);
        var end = new Date(rangeEnd);
        var weeks = Math.ceil((end - start) / (7 * 24 * 3600 * 1000)) + 2;
        return LEFT_MARGIN + weeks * (CELL_SIZE + 1) + RIGHT_MARGIN;
    }

    function _computeContainerHeight() {
        return TOP_MARGIN + DAY_ROWS * (CELL_SIZE + 1) + 4;
    }

    function _emptyOption(message) {
        return {
            graphic: [{
                type: "text",
                left: "center",
                top: "middle",
                style: { text: message || "No submissions yet.", fontSize: 14, fill: _palette().label },
            }],
        };
    }

    function _buildOption(payload, formatDate) {
        var data = payload.heatmap || [];
        var rangeStart = payload.range_start;
        var rangeEnd = payload.range_end;

        var maxCount = 0;
        for (var i = 0; i < data.length; i++) {
            if (data[i][1] > maxCount) maxCount = data[i][1];
        }
        if (maxCount < 5) maxCount = 5;

        var pal = _palette();

        return {
            aria: { enabled: true },
            tooltip: {
                backgroundColor: pal.tooltipBg,
                borderColor: pal.tooltipBorder,
                textStyle: { color: pal.tooltipText },
                formatter: function (params) {
                    var count = params.value[1];
                    var label = count === 1 ? "1 submission" : count + " submissions";
                    var day = formatDate ? formatDate(params.value[0]) : params.value[0];
                    return day + "<br/><strong>" + label + "</strong>";
                },
            },
            visualMap: {
                min: 0,
                max: maxCount,
                show: false,
                type: "piecewise",
                inRange: { color: pal.ramp },
            },
            calendar: {
                top: TOP_MARGIN,
                left: LEFT_MARGIN,
                right: RIGHT_MARGIN,
                cellSize: [CELL_SIZE, CELL_SIZE],
                range: [rangeStart, rangeEnd],
                itemStyle: {
                    color: pal.emptyCell,
                    borderColor: pal.cellBorder,
                    borderWidth: 0.5,
                },
                yearLabel: { show: false },
                monthLabel: { show: true, fontSize: 10, color: pal.label },
                dayLabel: { fontSize: 10, firstDay: 0, color: pal.label },
                splitLine: { show: false },
            },
            series: {
                type: "heatmap",
                coordinateSystem: "calendar",
                data: data,
            },
        };
    }

    function _create(containerId) {
        if (typeof echarts === "undefined") {
            console.error("ArenaSubmissionHeatmap: echarts is not loaded.");
            return null;
        }
        _dispose(containerId);
        var container = document.getElementById(containerId);
        if (!container) {
            console.error("ArenaSubmissionHeatmap: container #" + containerId + " not found.");
            return null;
        }
        var mgr = NocaECharts.create(container);
        _instances[containerId] = mgr;
        return { container: container, mgr: mgr };
    }

    function _renderPayload(instance, payload, options) {
        var opts = options || {};
        instance.mgr.hideLoading();
        if (!payload || !payload.heatmap || payload.heatmap.length === 0) {
            instance.mgr.render(function (chart) { chart.setOption(_emptyOption(opts.emptyMessage), true); });
            return;
        }
        var w = _computeContainerWidth(payload.range_start, payload.range_end);
        var h = _computeContainerHeight();
        instance.container.style.width = w + "px";
        instance.container.style.height = h + "px";
        instance.mgr.resize({ width: w, height: h });
        instance.mgr.render(function (chart) { chart.setOption(_buildOption(payload, opts.formatDate), true); });
    }

    function init(containerId, dataUrl) {
        var instance = _create(containerId);
        if (!instance) return;
        instance.mgr.showLoading();

        fetch(dataUrl)
            .then(function (response) {
                if (!response.ok) throw new Error("HTTP " + response.status);
                return response.json();
            })
            .then(function (payload) {
                _renderPayload(instance, payload);
            })
            .catch(function (err) {
                instance.mgr.hideLoading();
                console.error("ArenaSubmissionHeatmap: failed to load data.", err);
            });
    }

    function render(containerId, payload, options) {
        var instance = _create(containerId);
        if (!instance) return;
        _renderPayload(instance, payload, options);
    }

    function initDeclaredCharts() {
        document.querySelectorAll("[data-arena-submission-heatmap]").forEach(function (container) {
            init(container.id, container.dataset.heatmapUrl);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initDeclaredCharts);
    } else {
        initDeclaredCharts();
    }

    return { init: init, render: render, dispose: _dispose };
}());
