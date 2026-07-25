# Contest reports page

The contest reports page (`/c/{slug}/reports`) is the analytics dashboard for a
single contest. It aggregates every judged submission into a set of summary
tables and charts so admins and judges can see, at a glance, how many runs each
problem received, how they were verdicted, which languages contestants used, and
how activity was spread across teams and time.

This document explains what each table and chart on that page means, how its
numbers are computed, and how to read the cells.

## Who can see it

Access is limited to the **uberadmin**, **admin**, and **judge** roles for the
contest. The page also has two empty states:

- If the contest has not started yet, the page shows "Reports are only available
  after the contest starts."
- If the contest has started but nothing has been judged, it shows "No judged
  submissions yet."

## What counts as a "run"

Every table and chart is built from the same population of submissions, computed
in `web/services/contest_report_service/computation.py`:

- A submission is counted only when its **active judgment** is complete
  (`JudgmentStatus.DONE`) and has a final verdict. Pending, in-flight, or
  superseded judgments are ignored.
- "Runs" therefore means **judged runs**. The total appears throughout as
  `total_runs`.
- Problems are labeled by their contest ordinal (A, B, C, and so on) and carry
  their balloon color; languages carry their registry name and icon.

## Accepted, and the AC + PE toggle

Several tables report an "accepted" count. What qualifies depends on the
contest's `accept_pe` setting:

- By default, only **AC** (Accepted) counts, and the column is labeled `AC`.
- If the contest accepts presentation errors, **PE** also counts as accepted,
  and the column label becomes `AC + PE`.

Wherever you see `accept_label` below, read it as `AC` or `AC + PE` depending on
this setting.

## How to read a cell

Most cross-table cells share one format, produced by the `cv()` template macro:

- A non-zero cell shows the **count** followed by a **percentage** in
  parentheses, for example `12 (34.5%)`.
- A zero cell shows a muted dash (`-`).

The percentage's denominator is stated for each table below, because it differs
between reports (some are a share of the whole contest, others a share of a
single row).

## The tables and charts

The page renders nine reports, top to bottom.

### 1. Problem summary

A one-row-per-problem table of raw counts:

- **Runs:** total judged runs for the problem.
- **AC:** accepted-count for the problem. The percentage is the problem's AC
  rate (AC divided by that problem's runs).
- **AC + PE:** shown only when the contest accepts PE, using the same
  denominator.

The footer totals the runs, AC, and (when shown) AC + PE across all problems.

### 2. Runs distribution by problem

Shows how the contest's total run volume is split across problems, as both a
table and a pie chart.

- **Runs:** judged runs for the problem.
- **% Total:** the problem's share of `total_runs` (all judged runs in the
  contest). The column sums to 100%.

The accompanying pie chart uses each problem's balloon color for its slice.

### 3. `AC + PE` runs distribution by problem

The same as report 2, but restricted to accepted runs.

- The count column is labeled `AC` or `AC + PE`.
- **% Total:** the problem's share of all accepted runs in the contest. The
  column sums to 100%.

This report also has a pie chart colored by problem.

### 4. Runs by problem and verdict

A cross-table with one row per problem and one column per verdict (AC, WA, TLE,
and so on, from the full verdict list).

- Each cell is the count of runs for that problem with that verdict, and the
  percentage is that verdict's share of the problem's runs (the row).
- The **Total** column is the problem's total runs.
- The footer row totals each verdict column across all problems, with each
  percentage being that verdict's share of `total_runs`.

Use this table to see, per problem, where runs landed: how many were accepted
versus wrong-answer, timed out, and so on.

### 5. Runs by problem and language

A cross-table with one row per problem and one column per active language.

- Each cell is the count of runs for that problem in that language; the
  percentage is that language's share of the problem's runs (the row).
- The **Total** column is the problem's total runs.
- The footer totals each language column across all problems, with each
  percentage being that language's share of `total_runs`.

This shows the language mix contestants chose for each problem.

### 6. Runs by language and verdict

A cross-table with one row per language and one column per verdict.

- Each cell is the count of runs in that language with that verdict; the
  percentage is that verdict's share of the language's runs (the row).
- The **Total** column is the language's total runs.
- The footer totals each verdict column across all languages, with each
  percentage being that verdict's share of `total_runs`.

Use this to compare how different languages fared: for example, whether one
language shows a higher timeout rate.

### 7. Runs by team and problem

A team-by-problem matrix. Each row is one participating team, sorted by total
submissions in descending order, so the busiest teams appear first.

- The **Team** column shows the team's site identity plus its login, for example
  `Site name / Team full name (username)`.
- Each problem column is the count of that team's runs against the problem; the
  percentage is that problem's share of the **team's** total runs (the row).
- **Total:** the team's total judged runs across all problems.
- **`AC` / `AC + PE`:** how many of the team's runs were accepted, as a share of
  the team's total runs.

Reading across a row tells you how a team spread its attempts among the
problems; the accepted column tells you how many of all its runs succeeded.

### 8. Runs by time (10-minute windows)

A bar chart of all judged runs bucketed into 10-minute windows, based on each
submission's contest-relative timestamp (in seconds, divided into 600-second
windows). Submissions without a valid non-negative contest timestamp are
excluded from the time charts. This shows the overall submission activity curve
across the contest.

### 9. `AC + PE` runs by time (10-minute windows)

The same 10-minute windows as report 8, but counting only accepted runs. Compare
it with report 8 to see when accepted solutions arrived relative to total
submission volume.

## Where the code lives

- Route: `web/routes/contest_reports.py`
- Aggregation: `web/services/contest_report_service/computation.py`
- Table builders and DTOs: `web/services/contest_report_service/tables.py` and
  `models.py`
- Template: `web/template/admin/reports.html`
- Charts: `web/static/js/reports-charts.js` (rendered with ECharts)
