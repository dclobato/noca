//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

/**
 * Micro-histograms of AC-rate distribution, one per problem set.
 *
 * These live inside a legend table row at roughly 7.5rem wide. Both axes are
 * drawn and scaled -- x over the 0-100% AC-rate domain, y over the student count
 * -- but there is no room for a label per bin, so x is labelled only at 0, 50
 * and 100 and the hover tooltip carries the exact bin and count. The bin counts
 * are rendered into `data-counts` by the page rather than fetched, since the
 * report already computed them server-side; a chart per row must not become a
 * request per row.
 *
 * `data-y-max` is the tallest bin across every chart on the page, shared so the
 * charts are read against one another rather than each against itself.
 *
 * Markup contract:
 *   <div data-ac-rate-histogram data-counts="[0,1,...]" data-y-max="24"></div>
 */
var ArenaAcRateHistogram = (function () {
    function _binLabel(index, total) {
        var width = 100 / total;
        var low = Math.round(index * width);
        var high = Math.round((index + 1) * width);
        return low + "-" + high + "%";
    }

    function _buildOption(counts, yMax) {
        var tokens = NocaECharts.tokens();
        return {
            animation: false,
            tooltip: {
                trigger: "axis",
                axisPointer: { type: "shadow" },
                confine: true,
                formatter: function (params) {
                    var p = params[0];
                    var students = p.value === 1 ? "1 student" : p.value + " students";
                    return p.name + "<br/><strong>" + students + "</strong>";
                },
            },
            // No title and no legend (one series names itself), but both axes are
            // scaled: x is the 0-100% AC-rate domain, y is the student count. At
            // this size only the endpoints and midpoint of x can be labelled
            // legibly, so the ticks are thinned rather than shrunk into a blur.
            grid: { left: 2, right: 2, top: 4, bottom: 2, containLabel: true },
            xAxis: {
                type: "category",
                data: counts.map(function (_, i) { return _binLabel(i, counts.length); }),
                axisLine: { show: true, lineStyle: { color: tokens.axis } },
                axisTick: { show: false },
                axisLabel: {
                    show: true,
                    fontSize: 9,
                    color: tokens.label,
                    margin: 4,
                    interval: function (index) {
                        return index === 0 || index === counts.length - 1 || index * 2 === counts.length;
                    },
                    formatter: function (_value, index) {
                        if (index === 0) return "0";
                        return index === counts.length - 1 ? "100" : String(Math.round(index * (100 / counts.length)));
                    },
                },
                splitLine: { show: false },
            },
            yAxis: {
                type: "value",
                min: 0,
                // Shared across every set on the page, so bar heights compare
                // set to set instead of each chart rescaling to its own peak.
                max: yMax > 0 ? yMax : null,
                minInterval: 1,
                splitNumber: 2,
                axisLine: { show: false },
                axisTick: { show: false },
                axisLabel: { show: true, fontSize: 9, color: tokens.label, margin: 3 },
                splitLine: { show: true, lineStyle: { color: tokens.split } },
            },
            series: [
                {
                    type: "bar",
                    data: counts,
                    barCategoryGap: "18%",
                    itemStyle: {
                        color: tokens.palette[0],
                        borderRadius: [2, 2, 0, 0],
                    },
                },
            ],
        };
    }

    function init(container) {
        var counts;
        try {
            counts = JSON.parse(container.dataset.counts || "[]");
        } catch (err) {
            console.error("ArenaAcRateHistogram: unreadable data-counts.", err);
            return;
        }
        if (!Array.isArray(counts) || counts.length === 0) return;

        var yMax = parseInt(container.dataset.yMax, 10);
        var mgr = NocaECharts.create(container);
        mgr.render(function (chart) {
            chart.setOption(_buildOption(counts, isNaN(yMax) ? 0 : yMax), true);
        });
    }

    function initDeclaredCharts() {
        if (typeof echarts === "undefined") {
            console.error("ArenaAcRateHistogram: echarts is not loaded.");
            return;
        }
        document.querySelectorAll("[data-ac-rate-histogram]").forEach(init);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initDeclaredCharts);
    } else {
        initDeclaredCharts();
    }

    return { init: init };
}());
