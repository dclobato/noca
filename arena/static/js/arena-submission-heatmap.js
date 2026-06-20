//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

var ArenaSubmissionHeatmap = (function () {
    var _instances = {};
    var CELL_SIZE = 13;
    var LEFT_MARGIN = 30;
    var RIGHT_MARGIN = 10;
    var TOP_MARGIN = 20;
    var DAY_ROWS = 7;

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

    function _buildOption(payload) {
        var data = payload.heatmap || [];
        var rangeStart = payload.range_start;
        var rangeEnd = payload.range_end;

        var maxCount = 0;
        for (var i = 0; i < data.length; i++) {
            if (data[i][1] > maxCount) maxCount = data[i][1];
        }
        if (maxCount < 5) maxCount = 5;

        return {
            tooltip: {
                formatter: function (params) {
                    var count = params.value[1];
                    var label = count === 1 ? "1 submission" : count + " submissions";
                    return params.value[0] + "<br/><strong>" + label + "</strong>";
                },
            },
            visualMap: {
                min: 0,
                max: maxCount,
                show: false,
                type: "piecewise",
            },
            calendar: {
                top: TOP_MARGIN,
                left: LEFT_MARGIN,
                right: RIGHT_MARGIN,
                cellSize: [CELL_SIZE, CELL_SIZE],
                range: [rangeStart, rangeEnd],
                itemStyle: { borderWidth: 0.5 },
                yearLabel: { show: false },
                monthLabel: { show: true, fontSize: 10 },
                dayLabel: { fontSize: 10, firstDay: 0 },
                splitLine: { show: false },
            },
            series: {
                type: "heatmap",
                coordinateSystem: "calendar",
                data: data,
            },
        };
    }

    function init(containerId, dataUrl) {
        if (typeof echarts === "undefined") {
            console.error("ArenaSubmissionHeatmap: echarts is not loaded.");
            return;
        }

        _dispose(containerId);

        var container = document.getElementById(containerId);
        if (!container) {
            console.error("ArenaSubmissionHeatmap: container #" + containerId + " not found.");
            return;
        }

        var chart = echarts.init(container);
        _instances[containerId] = chart;

        chart.showLoading();

        fetch(dataUrl)
            .then(function (response) {
                if (!response.ok) throw new Error("HTTP " + response.status);
                return response.json();
            })
            .then(function (payload) {
                chart.hideLoading();
                if (!payload.heatmap || payload.heatmap.length === 0) {
                    chart.setOption({
                        graphic: [{
                            type: "text",
                            left: "center",
                            top: "middle",
                            style: { text: "No submissions yet.", fontSize: 14, fill: "#999" },
                        }],
                    });
                    return;
                }
                var w = _computeContainerWidth(payload.range_start, payload.range_end);
                var h = _computeContainerHeight();
                container.style.width = w + "px";
                container.style.height = h + "px";
                chart.resize({ width: w, height: h });
                chart.setOption(_buildOption(payload));
            })
            .catch(function (err) {
                chart.hideLoading();
                console.error("ArenaSubmissionHeatmap: failed to load data.", err);
            });
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

    return { init: init, dispose: _dispose };
}());
