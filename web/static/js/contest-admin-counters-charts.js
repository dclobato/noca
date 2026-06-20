// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Initializes ECharts stacked bar charts for contest admin counters page.
 */
document.addEventListener("DOMContentLoaded", function () {
    "use strict";

    var raw = document.getElementById("contest-admin-counters-chart-data");
    if (!raw || typeof echarts === "undefined") {
        return;
    }

    var data;
    try {
        data = JSON.parse(raw.textContent || "{}");
    } catch (_e) {
        return;
    }

    var charts = [];

    function buildStackedOption(group) {
        var segments = Array.isArray(group.segments)
            ? group.segments.filter(function (segment) {
                return Number(segment.value) > 0;
            })
            : [];

        return {
            tooltip: {
                trigger: "axis",
                axisPointer: {
                    type: "shadow",
                },
            },
            legend: {
                top: 0,
            },
            grid: {
                left: "3%",
                right: "4%",
                bottom: "3%",
                top: 35,
                containLabel: true,
            },
            xAxis: {
                type: "value",
                minInterval: 1,
            },
            yAxis: {
                type: "category",
                data: [group.category],
            },
            series: segments.map(function (segment) {
                return {
                    name: segment.name,
                    type: "bar",
                    stack: "total",
                    emphasis: {
                        focus: "series",
                    },
                    label: {
                        show: true,
                        formatter: function (params) {
                            return Number(params.value) > 0 ? String(params.value) : "";
                        },
                    },
                    data: [Number(segment.value)],
                };
            }),
        };
    }

    function initChart(elementId, group) {
        var element = document.getElementById(elementId);
        if (!element || !group) {
            return;
        }
        var chart = echarts.init(element);
        chart.setOption(buildStackedOption(group));
        charts.push(chart);
    }

    initChart("chart-counters-runs", data.runs);
    initChart("chart-counters-tasks", data.tasks);
    initChart("chart-counters-clarifications", data.clarifications);

    window.addEventListener("resize", function () {
        charts.forEach(function (chart) {
            chart.resize();
        });
    });
});
