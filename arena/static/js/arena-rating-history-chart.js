//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

var ArenaRatingChart = (function () {
    var _instances = {};

    function _dispose(containerId) {
        if (_instances[containerId]) {
            _instances[containerId].dispose();
            delete _instances[containerId];
        }
    }

    function _formatDate(isoString) {
        var d = new Date(isoString);
        return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
    }

    function _displayDate(point) {
        return point[2] || _formatDate(point[0]);
    }

    function _formatValue(value, valueKind) {
        if (valueKind === "difficulty") {
            return Number(value).toFixed(1);
        }
        return Math.round(Number(value)) + " pts";
    }

    function _valueLabel(valueKind) {
        return valueKind === "difficulty" ? "Difficulty" : "Rating";
    }

    function _buildOption(history, mode, valueKind) {
        var data = history.map(function (row) {
            return [row.ts, row.rating, row.ts_display || null];
        });

        if (mode === "sparkline") {
            return {
                animation: false,
                tooltip: {
                    trigger: "axis",
                    formatter: function (params) {
                        var p = params[0];
                        return _displayDate(p.value) + "<br/><strong>" + _formatValue(p.value[1], valueKind) + "</strong>";
                    },
                },
                grid: { left: 2, right: 2, top: 4, bottom: 4 },
                xAxis: {
                    type: "time",
                    boundaryGap: false,
                    show: false,
                },
                yAxis: {
                    type: "value",
                    scale: true,
                    show: false,
                },
                series: [
                    {
                        type: "line",
                        smooth: true,
                        data: data,
                        symbol: data.length === 1 ? "circle" : "none",
                        symbolSize: data.length === 1 ? 7 : 0,
                        lineStyle: { width: 2 },
                        itemStyle: { color: "#198754" },
                        areaStyle: { opacity: 0.06 },
                    },
                ],
            };
        }

        // "full" mode is identical to "default" but renders the entire range
        // with no zoom slider (used by the problem statistics page).
        var withZoom = mode !== "full";
        var option = {
            tooltip: {
                trigger: "axis",
                formatter: function (params) {
                    var p = params[0];
                    return _displayDate(p.value) + "<br/><strong>" + _formatValue(p.value[1], valueKind) + "</strong>";
                },
            },
            grid: { left: 60, right: 20, top: 20, bottom: withZoom ? 60 : 40 },
            xAxis: {
                type: "time",
                boundaryGap: false,
            },
            yAxis: {
                type: "value",
                min: 0,
                name: _valueLabel(valueKind),
                nameLocation: "middle",
                nameGap: 45,
            },
            series: [
                {
                    type: "line",
                    smooth: true,
                    data: data,
                    symbol: "circle",
                    symbolSize: 5,
                    lineStyle: { width: 2 },
                    areaStyle: { opacity: 0.08 },
                },
            ],
        };
        if (withZoom) {
            option.dataZoom = [
                { type: "inside", start: 75, end: 100 },
                { type: "slider", start: 75, end: 100, bottom: 8 },
            ];
        }
        return option;
    }

    function init(containerId, dataUrl, mode) {
        if (typeof echarts === "undefined") {
            console.error("ArenaRatingChart: echarts is not loaded.");
            return;
        }

        _dispose(containerId);

        var container = document.getElementById(containerId);
        if (!container) {
            console.error("ArenaRatingChart: container #" + containerId + " not found.");
            return;
        }

        var valueKind = container.dataset.ratingChartValueKind || "points";

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
                if (!payload.history || payload.history.length === 0) {
                    chart.setOption({
                        graphic: [{
                            type: "text",
                            left: "center",
                            top: "middle",
                            style: {
                                text:
                                    mode === "sparkline"
                                        ? "No " + _valueLabel(valueKind).toLowerCase() + " history"
                                        : "No " + _valueLabel(valueKind).toLowerCase() + " history available yet.",
                                fontSize: mode === "sparkline" ? 11 : 14,
                                fill: "#999",
                            },
                        }],
                    });
                    return;
                }
                chart.setOption(_buildOption(payload.history, mode, valueKind));
            })
            .catch(function (err) {
                chart.hideLoading();
                console.error("ArenaRatingChart: failed to load data.", err);
            });

        window.addEventListener("resize", function () {
            chart.resize();
        });
    }

    function initDeclaredCharts() {
        document.querySelectorAll("[data-arena-rating-chart]").forEach(function (container) {
            init(container.id, container.dataset.ratingChartUrl, container.dataset.ratingChartMode || "default");
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initDeclaredCharts);
    } else {
        initDeclaredCharts();
    }

    return { init: init, dispose: _dispose };
}());
