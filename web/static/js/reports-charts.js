// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
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
 *   - time_all:      [count, ...]   (total runs per window; cumulative is
 *                                   derived client-side)
 *   - time_accepted: [count, ...]   (accepted runs per window; cumulative
 *                                   is derived client-side)
 *   - time_window_minutes: bucket width in minutes (e.g. 10, 20, 480)
 *   - accept_label:  "AC" or "AC + PE"
 *   - problem_race:  [{name, color, solved_minutes: [minute, ...]}, ...]
 *                     (one entry per solving team, sorted ascending; the
 *                     cumulative step curve is derived client-side)
 *   - duration_minutes: contest length in minutes, for the race chart's
 *                     x-axis extent (a late solve past the nominal end
 *                     still widens it)
 *   - elapsed_minutes: contest-relative "now" while the contest runs, null
 *                     otherwise; the two time-axis charts mark it and shade
 *                     the stretch beyond it, which has not happened yet
 *   - solved_histogram_labels/_counts: parallel arrays, one bar per solved
 *                     count from 0 up to the field's maximum
 *   - solved_boxplot: [min, q1, median, q3, max] over active teams' solved
 *                     counts, or null when fewer than two active teams
 *                     exist (matches `PerformanceSummary.solved_summary`).
 *                     Drawn as a band, a median line and two whiskers over
 *                     the histogram above rather than as a chart of its own
 */
document.addEventListener("DOMContentLoaded", function () {
    "use strict";

    function token(name) {
        return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    }

    // The AC/Non-AC/cumulative series colours are domain-specific (verdict
    // semantics), so they come from noca's own tokens rather than
    // NocaECharts's generic categorical palette. Read fresh on every call,
    // not memoized: the footer's theme toggle flips data-bs-theme without a
    // page reload, NocaECharts.create() then disposes and re-renders each
    // chart, and getComputedStyle must be re-queried at that moment to pick
    // up the new theme's values -- a value captured once at page load would
    // otherwise stay stale after the very first toggle (e.g. a line drawn in
    // the old theme's dark ink, now invisible against the new dark surface).
    function seriesColors() {
        return {
            accepted: token("--noca-success") || "#2f9e41",
            rejected: token("--noca-on-surface-variant") || "#64748b",
            // Cumulative AC previously reused the brand-container green,
            // which sits only ~1.35:1 from the AC bar's green (both
            // saturated, similar lightness) -- the line all but vanished
            // wherever it crossed the bars. Neither cumulative line is green
            // now, so both stay legible against the green/gray bars.
            cumAll: token("--noca-on-surface") || "#1e293b",
            cumAccepted: token("--noca-info") || "#0891b2",
        };
    }

    // Colours for the solved-distribution chart and the summary drawn over
    // it. Read fresh on every render for the same reason `seriesColors()` is
    // -- see its comment. The band is the brand green at low alpha, built
    // from the `--noca-brand-rgb` token because a CSS colour cannot carry
    // the alpha ECharts needs here; it is lifted on the dark surface, where
    // the same alpha over a near-black ground would read as nothing at all.
    function summaryColors() {
        var brandRgb = token("--noca-brand-rgb") || "47, 158, 65";
        var dark = document.documentElement.getAttribute("data-bs-theme") === "dark";
        return {
            bars: token("--noca-info") || "#0891b2",
            band: "rgba(" + brandRgb + ", " + (dark ? "0.26" : "0.15") + ")",
            median: token("--noca-brand") || "#2f9e41",
            whisker: token("--noca-on-surface-variant") || "#64748b",
            summaryText: token("--noca-on-surface-variant") || "#64748b",
        };
    }

    // ── Helper: the "not yet" overlay for the two time-axis charts ────────
    // Both are drawn over the contest's full duration rather than up to the
    // current minute, so the axis stays put across reloads and scopes and a
    // reader can see how much contest is left. The cost is that an empty
    // future window looks exactly like a window in which nobody submitted,
    // so a running contest marks where "now" is and shades everything past
    // it. `position` and `end` are in the coordinate space of the axis this
    // is bound to, which is why the caller supplies them: the race chart's
    // x axis is already in minutes, while the runs-by-time chart is a
    // category axis whose hidden twin this converts minutes into.
    //
    // It draws through an empty line series of its own rather than through
    // the data series, because a markArea belongs to a series and would
    // otherwise pick up its colour, legend entry and tooltip.
    function notYetOverlay(position, end, axisIndex) {
        var dark = document.documentElement.getAttribute("data-bs-theme") === "dark";
        var line = token("--noca-on-surface-variant") || "#64748b";
        return {
            name: "Not run yet",
            type: "line",
            xAxisIndex: axisIndex,
            data: [],
            silent: true,
            showSymbol: false,
            tooltip: { show: false },
            markArea: {
                silent: true,
                itemStyle: { color: dark ? "rgba(148, 163, 184, 0.12)" : "rgba(100, 116, 139, 0.08)" },
                label: {
                    show: true,
                    position: "insideTop",
                    color: line,
                    fontSize: 11,
                    formatter: "Not run yet",
                },
                data: [[{ xAxis: position }, { xAxis: end }]],
            },
            markLine: {
                silent: true,
                symbol: "none",
                label: {
                    color: line,
                    fontSize: 11,
                    formatter: "Now",
                },
                data: [{ xAxis: position, lineStyle: { color: line, width: 1, type: "dashed" } }],
            },
        };
    }

    var raw = document.getElementById("report-chart-data");
    if (!raw) return;

    var data;
    try {
        data = JSON.parse(raw.textContent);
    } catch (_e) {
        return;
    }

    // Removes the "chart unavailable" placeholder immediately before a chart
    // successfully mounts, so it never sits behind (or shows through) the
    // canvas ECharts renders into.
    function clearPlaceholder(el) {
        el.textContent = "";
    }

    // ── Helper: build a pie chart option ──────────────────────────────────
    // Text (labels, legend, tooltip) comes from the noca-light/noca-dark
    // theme NocaECharts registers, so it stays legible and updates on theme
    // toggle without this file tracking it.
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

    // ── Helper: build a stacked bar chart option ──────────────────────────
    // Total bar height is the window's run volume, split into accepted and
    // non-accepted (rejected) segments. When `cumulativeAll` and/or
    // `cumulativeAccepted` are provided, line series are added on a
    // right-hand Y-axis showing cumulative counts through each window.
    // `colors` is a fresh seriesColors() snapshot -- see the mount() call
    // site, which rebuilds it on every render, including theme-toggle
    // re-renders. Axis/legend/tooltip text again comes from the registered
    // noca-light/noca-dark theme, not from this function.
    function stackedBarOption(labels, accepted, rejected, acceptLabel, cumulativeAll, cumulativeAccepted, colors, elapsedMinutes, windowMinutes) {
        var series = [
            {
                name: acceptLabel,
                type: "bar",
                stack: "runs",
                data: accepted,
                itemStyle: { color: colors.accepted },
            },
            {
                name: "Non-" + acceptLabel,
                type: "bar",
                stack: "runs",
                data: rejected,
                itemStyle: { color: colors.rejected },
            },
        ];
        var yAxis = [
            {
                type: "value",
                name: "Runs",
                minInterval: 1,
            },
        ];
        var hasOverlay = false;
        if (cumulativeAll && cumulativeAll.length > 0) {
            series.push({
                name: "Cumulative runs",
                type: "line",
                yAxisIndex: 1,
                smooth: true,
                showSymbol: false,
                lineStyle: { width: 2 },
                itemStyle: { color: colors.cumAll },
                data: cumulativeAll,
            });
            hasOverlay = true;
        }
        if (cumulativeAccepted && cumulativeAccepted.length > 0) {
            series.push({
                name: "Cumulative " + acceptLabel,
                type: "line",
                yAxisIndex: 1,
                smooth: true,
                showSymbol: false,
                lineStyle: { width: 2 },
                itemStyle: { color: colors.cumAccepted },
                data: cumulativeAccepted,
            });
            hasOverlay = true;
        }
        if (hasOverlay) {
            yAxis.push({
                type: "value",
                name: "Cumulative",
                position: "right",
                splitLine: { show: false },
                minInterval: 1,
            });
        }
        var legendData = [acceptLabel, "Non-" + acceptLabel];
        if (cumulativeAll && cumulativeAll.length > 0) {
            legendData.push("Cumulative runs");
        }
        if (cumulativeAccepted && cumulativeAccepted.length > 0) {
            legendData.push("Cumulative " + acceptLabel);
        }

        // The bars sit on a category axis, one band per time window, so
        // "now" cannot be placed on it directly: the current minute almost
        // never falls on a band boundary. A hidden value axis spanning the
        // same bands carries the overlay instead -- window i covers
        // [i - 0.5, i + 0.5] there, so minute m is at m / bucketMinutes - 0.5,
        // and in a 20-minute window a contest 45 minutes in marks one-quarter
        // into the third bar.
        var xAxis = [
            {
                type: "category",
                data: labels,
                axisLabel: { rotate: 45, fontSize: 10 },
                name: "Minutes",
                nameLocation: "center",
                nameGap: 35,
                barCategoryGap: "2%",
            },
        ];
        var bucketMinutes = windowMinutes || 10;
        var lastBand = labels.length - 0.5;
        var nowBand = (elapsedMinutes || 0) / bucketMinutes - 0.5;
        if (elapsedMinutes !== null && elapsedMinutes !== undefined && nowBand < lastBand) {
            xAxis.push({ type: "value", min: -0.5, max: lastBand, show: false });
            series.push(notYetOverlay(nowBand, lastBand, 1));
        }

        return {
            tooltip: {
                trigger: "axis",
                axisPointer: { type: "shadow" },
            },
            legend: {
                data: legendData,
                top: 0,
            },
            grid: {
                left: "3%",
                right: "6%",
                // `containLabel` reliably reserves room for axis tick
                // labels but not for the axis `name` itself (a known
                // ECharts gap, most visible with `nameLocation: "center"`),
                // so a name sitting past this margin renders straight past
                // the container edge instead of wrapping inside it. Fixed
                // pixels here, not a percentage of the container height,
                // since the room needed is set by the rotated tick labels'
                // and the name's own font sizes, not by how tall the chart
                // div happens to be.
                bottom: 60,
                top: 40,
                containLabel: true,
            },
            xAxis: xAxis,
            yAxis: yAxis,
            series: series,
        };
    }

    // ── Helper: build the Problem Race chart option ───────────────────────
    // One step line per problem: cumulative distinct-solver count against
    // contest-relative time. `series[i].solved_minutes` is one entry per
    // solving team (already deduplicated server-side, sorted ascending), so
    // the cumulative curve is just "count so far" at each solve instant --
    // a real step function (`step: "end"`), not a smoothed approximation,
    // since a solve count only ever changes at the instant it happens. Every
    // line is extended flat to `maxMinute` (the contest duration, or a later
    // solve if one landed after the nominal end) so all curves share the
    // same right edge instead of stopping wherever their last solve fell.
    function problemRaceOption(problemRace, durationMinutes, acceptLabel, elapsedMinutes) {
        var maxMinute = durationMinutes || 0;
        problemRace.forEach(function (series) {
            if (series.solved_minutes.length > 0) {
                maxMinute = Math.max(maxMinute, series.solved_minutes[series.solved_minutes.length - 1]);
            }
        });

        var series = problemRace.map(function (p) {
            var points = [[0, 0]];
            var count = 0;
            p.solved_minutes.forEach(function (minute) {
                count += 1;
                points.push([minute, count]);
            });
            var last = points[points.length - 1];
            if (last[0] < maxMinute) {
                points.push([maxMinute, last[1]]);
            }
            return {
                name: p.name,
                type: "line",
                step: "end",
                showSymbol: false,
                lineStyle: { width: 2, color: p.color },
                itemStyle: { color: p.color },
                data: points,
            };
        });

        // The flat tail every curve carries to the shared right edge is real
        // history once the contest is over, but during it that tail runs
        // through time that has not happened; the overlay is what tells the
        // two apart. Its axis is the chart's own, already in minutes.
        if (elapsedMinutes !== null && elapsedMinutes !== undefined && elapsedMinutes < maxMinute) {
            series.push(notYetOverlay(elapsedMinutes, maxMinute, 0));
        }

        return {
            tooltip: { trigger: "axis" },
            // The overlay carries a name so ECharts can key it, but it is not
            // a problem and must not appear beside the problems in the legend.
            legend: {
                type: "scroll",
                top: 0,
                data: problemRace.map(function (p) {
                    return p.name;
                }),
            },
            grid: {
                left: "3%",
                right: "4%",
                // See the equivalent comment in `stackedBarOption`: fixed
                // pixels, not a percentage, so the axis name has real room
                // regardless of container height.
                bottom: 45,
                top: 40,
                containLabel: true,
            },
            xAxis: {
                type: "value",
                name: "Minutes",
                nameLocation: "center",
                nameGap: 28,
                min: 0,
                max: maxMinute,
            },
            yAxis: {
                type: "value",
                name: "Cumulative " + acceptLabel,
                minInterval: 1,
            },
            series: series,
        };
    }

    // ── Helper: the "Active Teams by Problems Solved" distribution ────────
    // One chart carrying both halves of the same question: the bars are how
    // many teams solved each count, and the five-number summary
    // ([min, q1, median, q3, max]) is drawn *over* them as a box plot lying
    // on its side -- a shaded Q1..Q3 band, a median line, and min/max
    // whiskers. They share an axis because they measure the same quantity,
    // and each statistic then sits directly above the bars it describes,
    // which a detached box beside the histogram could not show.
    //
    // The two need different kinds of axis, so the chart carries both. The
    // bars keep the category axis a histogram wants: one band per solved
    // count, labelled with that integer, bars centred in their band. The
    // summary cannot live on it, because Q1, the median and Q3 are usually
    // fractional (2.5 solved problems) and a category axis can only place a
    // mark at a whole category. It is drawn instead against a *second*,
    // hidden value axis spanning -0.5 to max+0.5 -- exactly the range the
    // category bands cover, since the buckets are consecutive integers from
    // zero -- so a mark at 2.5 lands midway between the bars for 2 and 3.
    // Making the visible axis a value axis instead would have been simpler
    // and wrong: its ticks step from its minimum, so a -0.5 start puts every
    // label half a bucket off, between the bars rather than under them.
    //
    // The summary numbers stay the ones Python computed, printed verbatim
    // beneath the chart, so the drawing can never disagree with the figures.
    function solvedDistributionOption(labels, counts, summary, colors) {
        var maxX = labels.length > 0 ? labels.length - 1 : 0;

        var series = [
            {
                name: "Active teams",
                type: "bar",
                data: counts,
                barMaxWidth: 56,
                itemStyle: { color: colors.bars },
            },
        ];

        // A five-number summary is optional: a scope where every active team
        // solved nothing still has bars worth drawing.
        if (summary && summary.length === 5) {
            // The overlay hangs off an empty line series bound to the hidden
            // axis. A markArea/markLine belongs to a series and resolves its
            // coordinates on that series' axes, so this is what lets the
            // summary use fractional positions while the bars keep their
            // categories. It draws nothing itself.
            var overlay = {
                name: "Summary",
                type: "line",
                xAxisIndex: 1,
                data: [],
                silent: true,
                showSymbol: false,
                tooltip: { show: false },
            };
            overlay.markArea = {
                silent: true,
                itemStyle: { color: colors.band },
                label: {
                    show: true,
                    position: "insideTop",
                    color: colors.summaryText,
                    fontSize: 11,
                    formatter: "Q1-Q3",
                },
                data: [[{ xAxis: summary[1] }, { xAxis: summary[3] }]],
            };
            overlay.markLine = {
                silent: true,
                symbol: "none",
                precision: 1,
                label: {
                    color: colors.summaryText,
                    fontSize: 11,
                    formatter: function (params) {
                        return params.name + " " + params.value;
                    },
                },
                data: [
                    {
                        name: "Median",
                        xAxis: summary[2],
                        lineStyle: { color: colors.median, width: 2, type: "solid" },
                    },
                    {
                        name: "Min",
                        xAxis: summary[0],
                        lineStyle: { color: colors.whisker, width: 1, type: "dashed" },
                    },
                    {
                        name: "Max",
                        xAxis: summary[4],
                        lineStyle: { color: colors.whisker, width: 1, type: "dashed" },
                    },
                ],
            };
            series.push(overlay);
        }

        return {
            tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
            // See the equivalent comment in `stackedBarOption`: fixed
            // pixels, not a percentage, so the axis name has real room
            // regardless of container height.
            // `top` leaves a band above the plot for the summary's own labels
            // (Min/Median/Max sit at the top of their lines), and `left` is
            // fixed pixels because `containLabel` reserves room for tick
            // labels but not for the rotated axis name beside them.
            grid: { left: 52, right: "4%", bottom: 45, top: 34, containLabel: true },
            xAxis: [
                {
                    type: "category",
                    data: labels,
                    name: "Problems Solved",
                    nameLocation: "center",
                    nameGap: 28,
                },
                // The overlay's coordinate system, drawn nowhere. Its range
                // is the span the category bands cover, so a fractional
                // quartile lands proportionally between two bars.
                {
                    type: "value",
                    min: -0.5,
                    max: maxX + 0.5,
                    show: false,
                },
            ],
            yAxis: {
                type: "value",
                // Written along the axis rather than above it. ECharts puts a
                // y-axis name at the top-left corner by default, which is
                // exactly where this chart's Min line label lands whenever the
                // minimum is at or near zero -- the two overprinted each other.
                // Everything else on the page keeps the default, since no
                // other chart draws anything in that corner.
                name: "Teams",
                nameLocation: "center",
                nameRotate: 90,
                nameGap: 34,
                minInterval: 1,
            },
            series: series,
        };
    }

    // Each chart mounts independently: a thrown error in one (a corrupt
    // option, an ECharts bug) must not stop the others from rendering, and
    // the div's placeholder text is the visible signal something went wrong.
    // NocaECharts.create() owns the chart instance from here on: it disposes
    // and re-invokes buildOption() itself whenever the footer toggle flips
    // data-bs-theme, so this file never has to listen for that separately.
    //
    // Every chart on this page is drawn finished rather than animated into
    // place. The entrance animation is a reading cost here, not a flourish:
    // the page carries eight charts that a reader scrolls past looking for a
    // number, and each one growing from zero means the figure is unreadable
    // for the first second of every scroll. It is set once here rather than
    // in each option builder so a chart added later cannot forget it, and
    // deliberately not in the shared `NocaECharts` helper, whose other
    // consumers (the arena rating history chart) animate on purpose.
    function mount(el, buildOption) {
        if (!el) return;
        try {
            clearPlaceholder(el);
            var mgr = NocaECharts.create(el);
            mgr.render(function (chart) {
                var option = buildOption();
                option.animation = false;
                chart.setOption(option);
            });
        } catch (_e) {
            /* NocaECharts.create()/setOption threw before the chart could
               mount: put the placeholder back so the failure stays visible. */
            el.textContent = "Chart unavailable. Reload the page to try again.";
        }
    }

    // ── Pie charts ───────────────────────────────────────────────────────
    var runsPieEl = document.getElementById("chart-runs-dist");
    if (data.runs_pie && data.runs_pie.length > 0) {
        mount(runsPieEl, function () {
            return pieOption("Runs", data.runs_pie);
        });
    }

    var acceptedPieEl = document.getElementById("chart-accepted-dist");
    var acceptLabel = data.accept_label || "AC";
    if (data.accepted_pie && data.accepted_pie.length > 0) {
        mount(acceptedPieEl, function () {
            return pieOption(acceptLabel + " Solves", data.accepted_pie);
        });
    }

    // ── Problem Race chart ─────────────────────────────────────────────────
    var problemRaceEl = document.getElementById("chart-problem-race");
    if (data.problem_race && data.problem_race.length > 0) {
        mount(problemRaceEl, function () {
            return problemRaceOption(
                data.problem_race,
                data.duration_minutes,
                acceptLabel,
                data.elapsed_minutes
            );
        });
    }

    // ── Performance: Problems Solved distribution + summary overlay ────────
    var solvedHistogramEl = document.getElementById("chart-solved-histogram");
    if (data.solved_histogram_labels && data.solved_histogram_labels.length > 0) {
        mount(solvedHistogramEl, function () {
            return solvedDistributionOption(
                data.solved_histogram_labels,
                data.solved_histogram_counts,
                data.solved_boxplot,
                summaryColors()
            );
        });
    }

    // ── Stacked bar chart (runs by time, AC vs non-AC) ────────────────────
    var runsTimeEl = document.getElementById("chart-runs-time");
    if (data.time_labels && data.time_all && data.time_accepted) {
        mount(runsTimeEl, function () {
            var rejected = data.time_all.map(function (total, i) {
                return Math.max(0, total - (data.time_accepted[i] || 0));
            });
            var runningAll = 0;
            var cumulativeAll = data.time_all.map(function (total) {
                runningAll += total;
                return runningAll;
            });
            var runningAc = 0;
            var cumulativeAccepted = data.time_accepted.map(function (count) {
                runningAc += count;
                return runningAc;
            });
            return stackedBarOption(
                data.time_labels,
                data.time_accepted,
                rejected,
                acceptLabel,
                cumulativeAll,
                cumulativeAccepted,
                seriesColors(),
                data.elapsed_minutes,
                data.time_window_minutes || 10
            );
        });
    }
});
