//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

var ArenaDifficultyDistribution = (function () {
    function _binLabels(payload) {
        var binWidth = payload.bin_width || 0.5;
        var bins = payload.bins || (payload.counts || []).length;
        var labels = [];
        for (var i = 0; i < bins; i++) {
            labels.push(((i + 1) * binWidth).toFixed(1));
        }
        return labels;
    }

    function _buildOption(payload) {
        return {
            tooltip: {
                trigger: "axis",
                axisPointer: { type: "shadow" },
                formatter: function (params) {
                    var p = params[0];
                    return "Difficulty ≤ " + p.name + "<br/><strong>" + p.value + " problem(s)</strong>";
                },
            },
            grid: { left: 50, right: 20, top: 16, bottom: 40 },
            xAxis: {
                type: "category",
                data: _binLabels(payload),
                name: "Difficulty",
                nameLocation: "middle",
                nameGap: 26,
            },
            yAxis: {
                type: "value",
                name: "Problems",
                nameLocation: "middle",
                nameGap: 36,
                minInterval: 1,
            },
            series: [
                {
                    type: "bar",
                    data: payload.counts,
                    barCategoryGap: "10%",
                    itemStyle: { color: "#198754" },
                },
            ],
        };
    }

    function init(containerId, dataUrl) {
        if (typeof echarts === "undefined") {
            console.error("ArenaDifficultyDistribution: echarts is not loaded.");
            return;
        }
        var container = document.getElementById(containerId);
        if (!container) {
            console.error("ArenaDifficultyDistribution: container #" + containerId + " not found.");
            return;
        }

        var chart = echarts.init(container);
        chart.showLoading();

        fetch(dataUrl)
            .then(function (response) {
                if (!response.ok) throw new Error("HTTP " + response.status);
                return response.json();
            })
            .then(function (payload) {
                chart.hideLoading();
                if (!payload.counts || payload.counts.length === 0 || payload.total_problems === 0) {
                    chart.setOption({
                        graphic: [{
                            type: "text",
                            left: "center",
                            top: "middle",
                            style: {
                                text: "No difficulty distribution available yet.",
                                fontSize: 14,
                                fill: "#999",
                            },
                        }],
                    });
                    return;
                }
                chart.setOption(_buildOption(payload));
            })
            .catch(function (err) {
                chart.hideLoading();
                console.error("ArenaDifficultyDistribution: failed to load data.", err);
            });

        window.addEventListener("resize", function () {
            chart.resize();
        });
    }

    function initDeclaredCharts() {
        document.querySelectorAll("[data-difficulty-distribution]").forEach(function (container) {
            init(container.id, container.dataset.distributionUrl);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initDeclaredCharts);
    } else {
        initDeclaredCharts();
    }

    return { init: init };
}());
