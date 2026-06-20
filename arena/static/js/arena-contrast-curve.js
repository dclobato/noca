//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

// Illustrative chart for the bimodal contrast transform on the rating help
// page. It plots the contrast curve c(r) = sigmoid(g * (logit(r) - logit(m)))
// for a handful of attempt counts, so readers can see how a higher gain (more
// attempts) steepens the curve and pushes difficulty toward the easy/hard
// extremes. Curve constants are read from the container's data attributes so
// the illustration always matches the live rating constants.

var ArenaContrastCurve = (function () {
    var PIVOT = 0.5; // balanced population median used purely for illustration

    function _logit(x) {
        return Math.log(x / (1 - x));
    }

    function _sigmoid(z) {
        return 1 / (1 + Math.exp(-z));
    }

    function _gain(attempts, gainMax, scale) {
        return 1 + (gainMax - 1) * (1 - Math.exp(-attempts / scale));
    }

    function _curve(gain) {
        var pivotLogit = _logit(PIVOT);
        var points = [];
        // Stay just inside (0, 1) to keep logit finite.
        for (var i = 1; i < 100; i++) {
            var r = i / 100;
            var c = _sigmoid(gain * (_logit(r) - pivotLogit));
            points.push([r, c]);
        }
        return points;
    }

    function _buildOption(gainMax, scale) {
        var samples = [
            { attempts: 0, label: "g = 1 (no data)" },
            { attempts: scale, label: "g ≈ " + _gain(scale, gainMax, scale).toFixed(1) +
                " (" + scale + " attempts)" },
            { attempts: 3 * scale, label: "g ≈ " + _gain(3 * scale, gainMax, scale).toFixed(1) +
                " (" + (3 * scale) + " attempts)" },
        ];

        var series = samples.map(function (sample) {
            return {
                name: sample.label,
                type: "line",
                smooth: true,
                showSymbol: false,
                lineStyle: { width: sample.attempts === 0 ? 1.5 : 2.5 },
                data: _curve(_gain(sample.attempts, gainMax, scale)),
            };
        });

        return {
            tooltip: {
                trigger: "axis",
                valueFormatter: function (v) {
                    return Number(v).toFixed(2);
                },
            },
            legend: { orient: "vertical", right: 15, top: 75 },
            grid: { left: 55, right: 20, top: 16, bottom: 50 },
            xAxis: {
                type: "value",
                name: "Raw difficulty r",
                nameLocation: "middle",
                nameGap: 26,
                min: 0,
                max: 1,
            },
            yAxis: {
                type: "value",
                name: "Contrasted c",
                nameLocation: "middle",
                nameGap: 36,
                min: 0,
                max: 1,
            },
            series: series,
        };
    }

    function init(containerId) {
        if (typeof echarts === "undefined") {
            console.error("ArenaContrastCurve: echarts is not loaded.");
            return;
        }
        var container = document.getElementById(containerId);
        if (!container) {
            console.error("ArenaContrastCurve: container #" + containerId + " not found.");
            return;
        }

        var gainMax = parseFloat(container.dataset.contrastGainMax) || 4;
        var scale = parseFloat(container.dataset.contrastGainScale) || 25;

        var chart = echarts.init(container);
        chart.setOption(_buildOption(gainMax, scale));

        window.addEventListener("resize", function () {
            chart.resize();
        });
    }

    function initDeclaredCharts() {
        document.querySelectorAll("[data-contrast-curve]").forEach(function (container) {
            init(container.id);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initDeclaredCharts);
    } else {
        initDeclaredCharts();
    }

    return { init: init };
}());
