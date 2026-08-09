//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Browser-independent contract test for ceremony-render.js. A tiny DOM shim
// drives the real module, so column ordering, medal bands, focus, pending cells,
// and the modal-trigger contract are verified without a browser.

"use strict";

const assert = require("assert");
const path = require("path");

const render = require(path.join(__dirname, "..", "..", "..", "animator", "static", "js", "ceremony-render.js"));
const animate = require(path.join(__dirname, "..", "..", "..", "animator", "static", "js", "animator-animate.js"));

// ── Minimal DOM ──────────────────────────────────────────────────────────────
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.attributes = {};
    this.children = [];
    this.style = {};
    this._top = 0;
    this._text = "";
    this.classList = {
      add: (...classes) => {
        const current = (this.getAttribute("class") || "").split(/\s+/).filter(Boolean);
        classes.forEach((name) => {
          if (current.indexOf(name) === -1) {
            current.push(name);
          }
        });
        this.setAttribute("class", current.join(" "));
      },
      remove: (...classes) => {
        const current = (this.getAttribute("class") || "")
          .split(/\s+/)
          .filter((name) => name && classes.indexOf(name) === -1);
        this.setAttribute("class", current.join(" "));
      },
    };
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }
  removeAttribute(name) {
    delete this.attributes[name];
  }
  getBoundingClientRect() {
    return { top: this._top };
  }
  appendChild(child) {
    const existing = this.children.indexOf(child);
    if (existing !== -1) {
      this.children.splice(existing, 1);
    }
    this.children.push(child);
    return child;
  }
  removeChild(child) {
    this.children = this.children.filter((candidate) => candidate !== child);
    return child;
  }
  replaceChildren(...children) {
    this.children = children;
    this._text = "";
  }
  get firstChild() {
    return this.children.length ? this.children[0] : null;
  }
  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }
  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join("");
  }
}

function makeElement(tag) {
  return new Element(tag);
}

const doc = { createElement: makeElement };

function team(overrides) {
  return Object.assign(
    {
      team_id: "t1",
      team_name: "Team One",
      team_fullname: "Team One Full",
      site_name: null,
      current_rank: 1,
      solved: 1,
      penalty: 20,
      medal: null,
      problems: {},
    },
    overrides,
  );
}

function problem(overrides) {
  return Object.assign(
    {
      label: "A",
      problem_id: "p1",
      solved: false,
      attempts: 0,
      solved_at_minutes: null,
      penalty: 0,
      pending_frozen: false,
      pending_frozen_count: 0,
      is_first_solver: false,
    },
    overrides,
  );
}

function headerProblem(label, color) {
  return {
    label,
    color: color || "ff0000",
    problemId: "p-" + label.toLowerCase(),
    balloonBase: "/assets/balloon",
    starBase: "/assets/star",
  };
}

function draw(projection, options) {
  const tbody = makeElement("tbody");
  const headerRow = makeElement("tr");
  const labels = render.renderStandings(
    doc,
    tbody,
    headerRow,
    projection,
    Object.assign({ medalBase: "/assets/medal" }, options),
  );
  return { tbody, headerRow, labels };
}

function testHeaderUsesScoreboardBalloonsAndConfiguredOrder() {
  const projection = {
    teams: [
      team({
        problems: {
          A: problem({ label: "A", problem_id: "p-a" }),
          B: problem({ label: "B", problem_id: "p-b" }),
        },
      }),
    ],
  };
  const headerProblems = [
    { label: "B", color: "00aa00", problemId: "p-b", balloonBase: "/assets/balloon" },
    { label: "A", color: "cc0000", problemId: "p-a", balloonBase: "/assets/balloon" },
  ];
  const { headerRow, labels } = draw(projection, { headerProblems });

  assert.deepStrictEqual(labels, ["B", "A"], "metadata order matches the live scoreboard");
  const balloons = headerRow.children.slice(4).map((th) => th.children[0]);
  assert.deepStrictEqual(
    balloons.map((img) => img.getAttribute("src")),
    ["/assets/balloon/00aa00/B", "/assets/balloon/cc0000/A"],
  );
  assert.deepStrictEqual(
    balloons.map((img) => img.getAttribute("alt")),
    ["Problem B", "Problem A"],
  );
}

function testLeadingColumnsReuseScoreboardClasses() {
  const { headerRow, tbody } = draw({ teams: [team()] });
  const expected = [
    "animator-col-rank",
    "animator-col-team",
    "animator-col-solved",
    "animator-col-time",
  ];

  expected.forEach((className, index) => {
    assert.ok(headerRow.children[index].getAttribute("class").includes(className));
    assert.ok(tbody.children[0].children[index].getAttribute("class").includes(className));
  });
  assert.strictEqual(tbody.children[0].children[1].tagName, "TH");
  assert.strictEqual(tbody.children[0].children[1].getAttribute("scope"), "row");
}

// ── Column order is computed, not inherited ──────────────────────────────────
function testColumnOrderIsNatural() {
  assert.deepStrictEqual(["AA", "B", "Z"].sort(render.compareLabels), ["B", "Z", "AA"], "Z sorts before AA");

  // Union across teams, each with a different key order, and one team missing a
  // column entirely. Object-key iteration must not decide the board.
  const projection = {
    teams: [
      team({ team_id: "a", problems: { Z: problem({ label: "Z" }), B: problem({ label: "B" }) } }),
      team({ team_id: "b", problems: { AA: problem({ label: "AA" }), B: problem({ label: "B" }) } }),
    ],
  };
  assert.deepStrictEqual(render.problemLabels(projection), ["B", "Z", "AA"]);

  const { headerRow, tbody } = draw(projection);
  const headers = headerRow.children.map((c) => c.textContent);
  assert.deepStrictEqual(headers, ["#", "Team", "Solved", "Time", "B", "Z", "AA"]);
  // Both rows have the same width even though team "b" has no Z cell.
  assert.strictEqual(tbody.children[0].children.length, 7);
  assert.strictEqual(tbody.children[1].children.length, 7);
}

// ── Rows are drawn in authoritative order ────────────────────────────────────
function testAuthoritativeRowOrder() {
  const projection = {
    teams: [
      team({ team_id: "second", team_name: "Second", current_rank: 1 }),
      team({ team_id: "first", team_name: "First", current_rank: 2 }),
    ],
  };
  const { tbody } = draw(projection);
  assert.deepStrictEqual(
    tbody.children.map((row) => row.getAttribute("data-team-id")),
    ["second", "first"],
    "the renderer never re-sorts standings",
  );
}

// ── Team names are Bootstrap data-API modal triggers ─────────────────────────
function testTeamNameIsAModalTrigger() {
  const { tbody } = draw({
    teams: [team({ team_id: "t-42", team_name: "usp01", team_fullname: "Los <b>Bugs</b>" })],
  });
  const button = tbody.children[0].children[1].children[0];

  assert.strictEqual(button.tagName, "BUTTON");
  assert.strictEqual(button.getAttribute("type"), "button");
  // Focus restoration in Bootstrap 5.3 comes from the data API, not from show().
  assert.strictEqual(button.getAttribute("data-bs-toggle"), "modal");
  assert.strictEqual(button.getAttribute("data-bs-target"), render.MODAL_SELECTOR);
  assert.strictEqual(button.getAttribute("data-team-id"), "t-42");
  // A hostile name is text, never markup.
  assert.strictEqual(button.textContent, "Los <b>Bugs</b>");
}

// ── The projector shows the team's name, never its login ─────────────────────
function testTeamNameNotLogin() {
  const named = draw({ teams: [team({ team_name: "usp01", team_fullname: "Unicamp Alpha" })] });
  const button = named.tbody.children[0].children[1].children[0];
  assert.strictEqual(button.textContent, "Unicamp Alpha", "the audience reads the name, not the login");
  assert.strictEqual(button.getAttribute("title"), "Unicamp Alpha");
  assert.ok(!named.tbody.children[0].children[1].textContent.includes("usp01"));

  // A team with no full name still has to be identifiable, so the login is the
  // fallback rather than an empty cell.
  [undefined, "", "   "].forEach((missing) => {
    const fallback = draw({ teams: [team({ team_name: "usp01", team_fullname: missing })] });
    const cell = fallback.tbody.children[0].children[1].children[0];
    assert.strictEqual(cell.textContent, "usp01", "a nameless team falls back to its login");
  });
}

// ── Medals: bands, boundaries, and transitions ───────────────────────────────
function testMedalBandsAndBoundaries() {
  const teams = [
    team({ team_id: "g", current_rank: 1, medal: "gold" }),
    team({ team_id: "s", current_rank: 2, medal: "silver" }),
    team({ team_id: "b", current_rank: 3, medal: "bronze" }),
    team({ team_id: "n", current_rank: 4, medal: null }),
  ];
  const { tbody } = draw({ teams: teams });

  assert.deepStrictEqual(
    tbody.children.map((r) => r.getAttribute("data-medal")),
    ["gold", "silver", "bronze", null],
  );
  // Each medalled row here is the last of its band, so each carries the rule;
  // the unmedalled row never does.
  const bandEnds = tbody.children.map((r) => r.getAttribute("class").includes("animator-row--band-end"));
  assert.deepStrictEqual(bandEnds, [true, true, true, false]);
  // The numeric rank stays uncluttered while the team cell carries the
  // accessible watermark served by the animator's own /assets route.
  const goldRow = tbody.children[0];
  assert.strictEqual(goldRow.children[0].textContent, "1");
  assert.strictEqual(goldRow.children[0].children.length, 0);
  const watermark = goldRow.children[1].children.at(-1);
  assert.strictEqual(watermark.tagName, "IMG");
  assert.strictEqual(watermark.getAttribute("class"), "animator-medal-watermark");
  assert.strictEqual(watermark.getAttribute("src"), "/assets/medal/gold");
  assert.strictEqual(watermark.getAttribute("alt"), "gold medal");
  assert.ok(
    tbody.children[3].children[1].children.every((child) => child.tagName !== "IMG"),
    "an unmedalled team has no watermark",
  );

  // A wider gold band: only its last row ends the band.
  const wide = draw({
    teams: [
      team({ team_id: "g1", current_rank: 1, medal: "gold" }),
      team({ team_id: "g2", current_rank: 2, medal: "gold" }),
      team({ team_id: "s1", current_rank: 3, medal: "silver" }),
    ],
  });
  assert.deepStrictEqual(
    wide.tbody.children.map((r) => r.getAttribute("class").includes("animator-row--band-end")),
    [false, true, true],
  );
}

function testMedalTransitionBetweenProjections() {
  const tbody = makeElement("tbody");
  const headerRow = makeElement("tr");
  render.renderStandings(
    doc,
    tbody,
    headerRow,
    { teams: [team({ team_id: "x", medal: "silver" })] },
    { medalBase: "/assets/medal" },
  );
  const row = tbody.children[0];
  assert.strictEqual(row.getAttribute("data-medal"), "silver");
  assert.strictEqual(row.children[1].children.at(-1).getAttribute("src"), "/assets/medal/silver");

  render.renderStandings(
    doc,
    tbody,
    headerRow,
    { teams: [team({ team_id: "x", medal: "gold" })] },
    { medalBase: "/assets/medal" },
  );
  assert.strictEqual(tbody.children[0], row, "the keyed renderer retains the moving team row");
  assert.strictEqual(row.getAttribute("data-medal"), "gold");
  assert.strictEqual(row.children[1].children.at(-1).getAttribute("src"), "/assets/medal/gold");
}

// ── Focus follows the projection ─────────────────────────────────────────────
function testFocusFollowsProjection() {
  const teams = [team({ team_id: "a" }), team({ team_id: "b" })];
  const first = draw({ teams: teams, focused_team_id: "a" });
  assert.ok(first.tbody.children[0].getAttribute("class").includes("ceremony-row--focused"));
  assert.ok(!first.tbody.children[1].getAttribute("class").includes("ceremony-row--focused"));

  const second = draw({ teams: teams, focused_team_id: "b" });
  assert.ok(!second.tbody.children[0].getAttribute("class").includes("ceremony-row--focused"));
  assert.ok(second.tbody.children[1].getAttribute("class").includes("ceremony-row--focused"));

  const none = draw({ teams: teams, focused_team_id: null });
  assert.ok(none.tbody.children.every((r) => !r.getAttribute("class").includes("ceremony-row--focused")));
}

// ── Problem cell states ──────────────────────────────────────────────────────
function testProblemCellStates() {
  const pending = render.renderProblemCell(
    doc,
    problem({ pending_frozen_count: 3, attempts: 5, penalty: 100 }),
  );
  assert.ok(pending.getAttribute("class").includes("ceremony-cell--pending"));
  assert.deepStrictEqual(
    pending.children[0].children.map((line) => line.textContent),
    ["???", "−5", "(100')"],
    "each unrevealed submission gets a question mark above attempts and penalty",
  );
  assert.ok(pending.textContent.includes("3 unrevealed submissions, 5 failed attempts"));

  const failed = render.renderProblemCell(doc, problem({ attempts: 3, penalty: 60 }));
  assert.ok(failed.getAttribute("class").includes("animator-cell--attempted"));
  assert.ok(failed.textContent.includes("−3"), "a true minus sign, matching the live board");
  assert.ok(failed.textContent.includes("(60')"));

  const first = render.renderProblemCell(
    doc,
    problem({
      solved: true,
      attempts: 1,
      solved_at_minutes: 5,
      penalty: 20,
      is_first_solver: true,
    }),
    false,
    headerProblem("A"),
  );
  assert.ok(first.getAttribute("class").includes("animator-cell--first"));
  assert.strictEqual(first.children[0].children[0].getAttribute("src"), "/assets/star/ff0000");

  const untried = render.renderProblemCell(doc, problem({}));
  assert.ok(untried.textContent.includes("no attempts"));

  const missing = render.renderProblemCell(doc, undefined);
  assert.ok(missing.textContent.includes("no attempts"), "a team missing a cell renders an empty visual stack");
}

// ── Attempt counts are reported exactly, never adjusted ──────────────────────
// `attempts` is already "penalizing attempts made before the accepted
// submission" (ProblemResult.attempts in shared/services/scoreboard_projection.py).
// An off-by-one here silently understates every team's failures on a projector.
function testSolvedCellsReportAttemptsVerbatim() {
  const cases = [
    { attempts: 0, expected: null },
    { attempts: 1, expected: "+1 (20')" },
    { attempts: 2, expected: "+2 (40')" },
    { attempts: 7, expected: "+7 (140')" },
  ];
  cases.forEach(({ attempts, expected }) => {
    const td = render.renderProblemCell(
      doc,
      problem({ solved: true, attempts, solved_at_minutes: 42, penalty: attempts * 20 }),
      false,
      headerProblem("A"),
    );
    assert.ok(td.getAttribute("class").includes("animator-cell--solved"));
    assert.ok(td.textContent.includes("42'"));
    if (expected) {
      assert.ok(td.textContent.includes(expected), `a solve after ${attempts} failures renders ${expected}`);
    } else {
      assert.ok(!td.textContent.includes("+"), "a first-attempt solve has no solitary plus");
    }
  });
}

// A cell solved before the freeze can still hold an unrevealed frozen run. The
// solve is settled, so it keeps its glyph and only gains the pending marker.
function testSolvedAndPendingKeepsTheSolve() {
  const td = render.renderProblemCell(
    doc,
    problem({
      solved: true,
      attempts: 1,
      solved_at_minutes: 30,
      penalty: 20,
      pending_frozen_count: 2,
    }),
  );
  assert.ok(td.textContent.includes("??"), "settled solves show every later unrevealed submission");
  assert.ok(td.textContent.includes("30'"), "a settled solve is not hidden behind a question mark");
  assert.ok(td.textContent.includes("+1 (20')"));
  const classes = td.getAttribute("class");
  assert.ok(classes.includes("animator-cell--solved"));
  assert.ok(classes.includes("ceremony-cell--pending"), "but it is still marked as holding a frozen run");
}

// The projector and the live board must agree cell for cell.
function testSharedFormatterAgreesWithTheLiveBoard() {
  const format = require(path.join(__dirname, "..", "..", "..", "animator", "static", "js", "cell-format.js"));
  // Field-name aliases must not become behavior differences.
  assert.strictEqual(format.formatCellText({ solved: false, attempts: 2, is_pending: true }), "? −2");
  assert.strictEqual(format.formatCellText({ solved: false, attempts: 2, pending_frozen: true }), "? −2");
  assert.strictEqual(format.formatPendingMarks({ pending_frozen_count: 3 }), "???");
  assert.strictEqual(format.pendingCountOf({ pending_frozen_count: 3 }), 3);
  assert.strictEqual(format.formatCellText({ solved: true, attempts: 0 }), "");
  assert.strictEqual(format.isFirst({ is_first_balloon: true }), true);
  assert.strictEqual(format.isFirst({ is_first_solver: true }), true);
  // And the reveal renderer draws exactly what the formatter says.
  const view = problem({ solved: true, attempts: 3, solved_at_minutes: 11, penalty: 60 });
  assert.ok(render.renderProblemCell(doc, view).textContent.includes(format.formatCellText(view)));
}

// ── The next cell glows, and only that one ───────────────────────────────────
function testNextCellIsMarked() {
  const teams = [
    team({
      team_id: "a",
      problems: { A: problem({ label: "A", problem_id: "p1" }), B: problem({ label: "B", problem_id: "p2" }) },
    }),
    team({
      team_id: "b",
      problems: { A: problem({ label: "A", problem_id: "p1" }), B: problem({ label: "B", problem_id: "p2" }) },
    }),
  ];
  const { tbody } = draw({ teams: teams, next_cell: { team_id: "b", problem_id: "p2", label: "B" } });

  const marked = [];
  tbody.children.forEach((row) => {
    row.children.forEach((td) => {
      if (td.getAttribute("data-next") === "true") {
        marked.push(row.getAttribute("data-team-id") + ":" + td.getAttribute("data-problem-id"));
      }
    });
  });
  assert.deepStrictEqual(marked, ["b:p2"], "exactly one cell is announced as next");

  const glowing = tbody.children[1].children.filter((td) =>
    (td.getAttribute("class") || "").includes("ceremony-cell--next"),
  );
  assert.strictEqual(glowing.length, 1);
}

// The match is by problem id, so a relabelled column cannot move the glow.
function testNextCellMatchesOnProblemIdNotLabel() {
  const teams = [
    team({ team_id: "a", problems: { A: problem({ label: "A", problem_id: "p-alpha" }) } }),
  ];
  const byId = draw({ teams: teams, next_cell: { team_id: "a", problem_id: "p-alpha", label: "WRONG-LABEL" } });
  assert.strictEqual(byId.tbody.children[0].children[4].getAttribute("data-next"), "true");

  const wrongId = draw({ teams: teams, next_cell: { team_id: "a", problem_id: "p-other", label: "A" } });
  assert.strictEqual(wrongId.tbody.children[0].children[4].getAttribute("data-next"), null);
}

function testNoNextCellMeansNoGlow() {
  const teams = [team({ team_id: "a", problems: { A: problem({ label: "A", problem_id: "p1" }) } })];
  [undefined, null].forEach((value) => {
    const { tbody } = draw({ teams: teams, next_cell: value });
    tbody.children.forEach((row) =>
      row.children.forEach((td) => {
        assert.strictEqual(td.getAttribute("data-next"), null, "an idle or finished ceremony glows nowhere");
      }),
    );
  });
}

// ── Empty / not-started projections ──────────────────────────────────────────
function testEmptyProjection() {
  assert.deepStrictEqual(render.problemLabels(null), []);
  assert.deepStrictEqual(render.problemLabels({ teams: [] }), []);
  const { tbody, headerRow } = draw({ teams: [] });
  assert.strictEqual(tbody.children.length, 0);
  assert.strictEqual(headerRow.children.length, 4, "only the fixed columns remain");
  assert.strictEqual(render.renderProgress(null), "");
  assert.strictEqual(render.renderProgress({ revealed_count: 3, frozen_count: 9 }), "3 of 9 revealed");
}

// ── Re-render replaces, never appends ────────────────────────────────────────
function testRerenderReplacesRows() {
  const tbody = makeElement("tbody");
  const headerRow = makeElement("tr");
  const projection = { teams: [team({ team_id: "a" })] };
  render.renderStandings(doc, tbody, headerRow, projection, {});
  render.renderStandings(doc, tbody, headerRow, projection, {});
  assert.strictEqual(tbody.children.length, 1, "a reconnect must not duplicate the board");
  assert.strictEqual(headerRow.children.length, 4);
}

function testRerenderReusesAndMovesRowsByTeamId() {
  const tbody = makeElement("tbody");
  const headerRow = makeElement("tr");
  render.renderStandings(
    doc,
    tbody,
    headerRow,
    {
      teams: [
        team({ team_id: "a", current_rank: 1 }),
        team({ team_id: "b", current_rank: 2 }),
      ],
    },
    {},
  );
  const rowA = tbody.children[0];
  const rowB = tbody.children[1];

  render.renderStandings(
    doc,
    tbody,
    headerRow,
    {
      teams: [
        team({ team_id: "b", current_rank: 1 }),
        team({ team_id: "a", current_rank: 2 }),
      ],
    },
    {},
  );

  assert.strictEqual(tbody.children[0], rowB, "the promoted team keeps its painted row");
  assert.strictEqual(tbody.children[1], rowA, "the displaced team keeps its painted row");
  assert.deepStrictEqual(
    tbody.children.map((row) => row.children[0].textContent),
    ["1", "2"],
    "reused rows still receive their current rank",
  );
}

function testPromotedRowAnimatesFromItsPreviousPosition() {
  const tbody = makeElement("tbody");
  const headerRow = makeElement("tr");
  render.renderStandings(
    doc,
    tbody,
    headerRow,
    {
      teams: [
        team({ team_id: "a", current_rank: 1 }),
        team({ team_id: "b", current_rank: 2 }),
      ],
    },
    {},
  );
  tbody.children[0]._top = 100;
  tbody.children[1]._top = 150;
  const promotedRow = tbody.children[1];
  let frames = null;
  promotedRow.animate = (nextFrames) => {
    frames = nextFrames;
  };
  const applier = animate.createApplier(tbody, {
    rowAnimation: { duration: 1500 },
  });
  const firstTops = applier.measureRows();

  render.renderStandings(
    doc,
    tbody,
    headerRow,
    {
      teams: [
        team({ team_id: "b", current_rank: 1 }),
        team({ team_id: "a", current_rank: 2 }),
      ],
    },
    {},
  );
  tbody.children[0]._top = 100;
  tbody.children[1]._top = 150;
  applier.apply(null, firstTops);

  assert.strictEqual(tbody.children[0], promotedRow);
  assert.deepStrictEqual(frames, [
    { transform: "translateY(50px)" },
    { transform: "translateY(0)" },
  ]);
}

testColumnOrderIsNatural();
testHeaderUsesScoreboardBalloonsAndConfiguredOrder();
testLeadingColumnsReuseScoreboardClasses();
testAuthoritativeRowOrder();
testTeamNameIsAModalTrigger();
testTeamNameNotLogin();
testMedalBandsAndBoundaries();
testMedalTransitionBetweenProjections();
testFocusFollowsProjection();
testProblemCellStates();
testSolvedCellsReportAttemptsVerbatim();
testSolvedAndPendingKeepsTheSolve();
testSharedFormatterAgreesWithTheLiveBoard();
testNextCellIsMarked();
testNextCellMatchesOnProblemIdNotLabel();
testNoNextCellMeansNoGlow();
testEmptyProjection();
testRerenderReplacesRows();
testRerenderReusesAndMovesRowsByTeamId();
testPromotedRowAnimatesFromItsPreviousPosition();
console.log("ceremony-render contract: OK");
