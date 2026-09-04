//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

// Solver-side surfaces of the problem statistics page: the first/last solver
// facts, the attempts-to-solve histogram with its count and median readout, and the
// submission heatmap with its year selector. ArenaProblemStatistics owns the
// fetch and hands the payload here; every surface decides its own empty state
// from its own fields, so a pending-only problem still shows its heatmap.
// Each chart is mirrored in the DOM by an aria-live status line, because
// canvas pixels are invisible to screen readers.
var ArenaProblemSolverStats = (function () {
    var HEATMAP_ID = "problem-stats-heatmap";
    var ATTEMPTS_ID = "problem-stats-attempts";
    // "All years" folds every day onto one calendar; a leap year keeps Feb 29.
    var LEAP_ANCHOR = "2024";
    var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

    function _emptyOption(message) {
        return ArenaProblemStatistics.emptyOption(message);
    }

    function _setText(selector, text) {
        var el = document.querySelector(selector);
        if (el) el.textContent = text;
    }

    function _plural(count, singular, plural) {
        return count + " " + (count === 1 ? singular : plural);
    }

    function _renderSolver(selector, solver) {
        var el = document.querySelector(selector);
        if (!el) return;
        el.textContent = "";
        if (!solver) {
            el.textContent = "No solvers yet.";
            el.classList.add("text-muted");
            return;
        }
        el.classList.remove("text-muted");
        var name;
        if (solver.profile_url) {
            name = document.createElement("a");
            name.href = solver.profile_url;
        } else {
            name = document.createElement("span");
        }
        name.textContent = solver.name;
        el.appendChild(name);
        var when = document.createElement("small");
        when.className = "d-block text-muted fw-normal";
        when.textContent = solver.solved_at_display || solver.solved_at;
        el.appendChild(when);
    }

    function _renderSolvers(payload) {
        _renderSolver("[data-stats-first-solver]", payload.first_solver);
        _renderSolver("[data-stats-last-solver]", payload.last_solver);
    }

    // Categories come from each bin's own label; the edges are never restated here.
    function _attemptsOption(histogram) {
        return {
            aria: { enabled: true },
            tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
            grid: { left: 50, right: 20, top: 20, bottom: 50 },
            xAxis: {
                type: "category",
                data: histogram.map(function (bin) { return bin.label; }),
                name: "Submissions until first AC",
                nameLocation: "middle",
                nameGap: 32,
            },
            yAxis: { type: "value", name: "Solvers", minInterval: 1 },
            series: [{
                type: "bar",
                name: "Solvers",
                barCategoryGap: "20%",
                data: histogram.map(function (bin) { return bin.count; }),
            }],
        };
    }

    // solver_count is authoritative. The histogram covers the solvers whose first
    // AC submission is known, which is all of them unless a solver row carries no
    // AC judgment at all; the two are reconciled here rather than reporting the
    // smaller number as the solver total.
    function _attemptsStatus(solverCount, identified, median) {
        if (solverCount === 0) return "No solvers yet.";
        var text = _plural(solverCount, "solver", "solvers") + ".";
        if (identified === 0) {
            return text + (solverCount === 1 ? " Attempt count unknown." : " Attempt counts unknown.");
        }
        if (median !== null && median !== undefined) {
            text += " Median " + Number(median).toFixed(1) + " attempts";
            text += identified < solverCount ? ", over the " + identified + " with a known first accepted submission." : ".";
        }
        return text;
    }

    function _renderAttempts(chart, payload) {
        var histogram = payload.attempts_histogram || [];
        var identified = histogram.reduce(function (sum, bin) { return sum + (bin.count || 0); }, 0);
        var solverCount = payload.solver_count || 0;
        _setText(
            "[data-stats-attempts-status]",
            _attemptsStatus(solverCount, identified, payload.median_attempts)
        );
        if (!chart) return;
        chart.hideLoading();
        chart.render(function (instance) {
            if (identified === 0) {
                instance.setOption(
                    _emptyOption(solverCount === 0 ? "No solvers yet." : "No attempt counts available."),
                    true
                );
            } else {
                instance.setOption(_attemptsOption(histogram), true);
            }
        });
    }

    // Every calendar year between the first and last submission, newest first,
    // so a gap year is selectable and shows an empty calendar.
    function _yearsOf(heatmap) {
        if (!heatmap.first_date || !heatmap.last_date) return [];
        var years = [];
        for (var y = Number(heatmap.last_date.slice(0, 4)); y >= Number(heatmap.first_date.slice(0, 4)); y--) {
            years.push(String(y));
        }
        return years;
    }

    function _formatMonthDay(iso) {
        return MONTHS[Number(iso.slice(5, 7)) - 1] + " " + Number(iso.slice(8, 10));
    }

    function _toHeatmapPayload(days, year) {
        if (year !== null) {
            return {
                heatmap: days.filter(function (day) { return day[0].slice(0, 4) === year; }),
                range_start: year + "-01-01",
                range_end: year + "-12-31",
            };
        }
        var folded = {};
        days.forEach(function (day) {
            var key = LEAP_ANCHOR + day[0].slice(4);
            folded[key] = (folded[key] || 0) + day[1];
        });
        return {
            heatmap: Object.keys(folded).sort().map(function (key) { return [key, folded[key]]; }),
            range_start: LEAP_ANCHOR + "-01-01",
            range_end: LEAP_ANCHOR + "-12-31",
        };
    }

    function _describeHeatmap(view, year) {
        var total = view.heatmap.reduce(function (sum, day) { return sum + day[1]; }, 0);
        var scope = year === null ? "across all years, by day of the year" : "in " + year;
        if (total === 0) return "No submissions " + scope + ".";
        return _plural(total, "submission", "submissions") + " on " + _plural(view.heatmap.length, "day", "days") + " " + scope + ".";
    }

    function _drawHeatmap(days, year) {
        var view = _toHeatmapPayload(days, year);
        var formatDate = year === null ? _formatMonthDay : null;
        var options = { emptyMessage: "No submissions in this period." };
        if (formatDate) options.formatDate = formatDate;
        _setText("[data-stats-heatmap-status]", _describeHeatmap(view, year));
        ArenaSubmissionHeatmap.render(HEATMAP_ID, view, options);
    }

    function _renderYearSelector(toolbar, years, selected, onSelect) {
        toolbar.textContent = "";
        var choices = [{ value: null, label: "All years" }].concat(years.map(function (year) {
            return { value: year, label: year };
        }));
        var buttons = [];
        choices.forEach(function (choice) {
            var button = document.createElement("button");
            button.type = "button";
            button.className = "btn btn-sm btn-outline-secondary";
            button.textContent = choice.label;
            button.setAttribute("aria-pressed", String(choice.value === selected));
            if (choice.value === selected) button.classList.add("active");
            button.addEventListener("click", function () {
                buttons.forEach(function (other) {
                    other.classList.remove("active");
                    other.setAttribute("aria-pressed", "false");
                });
                button.classList.add("active");
                button.setAttribute("aria-pressed", "true");
                onSelect(choice.value);
            });
            buttons.push(button);
            toolbar.appendChild(button);
        });
    }

    function _renderHeatmap(payload) {
        var toolbar = document.querySelector("[data-stats-heatmap-years]");
        var heatmap = payload.submission_heatmap || {};
        var days = heatmap.days || [];
        var years = _yearsOf(heatmap);
        if (toolbar) toolbar.hidden = years.length === 0;
        if (years.length === 0) {
            _setText("[data-stats-heatmap-status]", "No submissions yet.");
            ArenaSubmissionHeatmap.render(HEATMAP_ID, { heatmap: [] });
            return;
        }
        var selected = years[0];
        if (toolbar) {
            _renderYearSelector(toolbar, years, selected, function (year) { _drawHeatmap(days, year); });
        }
        _drawHeatmap(days, selected);
    }

    function init() {
        var el = document.getElementById(ATTEMPTS_ID);
        var chart = el ? NocaECharts.create(el) : null;
        if (chart) chart.showLoading();

        function render(payload) {
            _renderSolvers(payload);
            _renderAttempts(chart, payload);
            _renderHeatmap(payload);
        }

        function renderError(message) {
            if (chart) {
                chart.hideLoading();
                chart.render(function (instance) { instance.setOption(_emptyOption(message), true); });
            }
            ["[data-stats-first-solver]", "[data-stats-last-solver]",
             "[data-stats-attempts-status]", "[data-stats-heatmap-status]"]
                .forEach(function (selector) { _setText(selector, message); });
            var toolbar = document.querySelector("[data-stats-heatmap-years]");
            if (toolbar) toolbar.hidden = true;
            ArenaSubmissionHeatmap.render(HEATMAP_ID, { heatmap: [] }, { emptyMessage: message });
        }

        return { render: render, renderError: renderError };
    }

    return { init: init };
}());
