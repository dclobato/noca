//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Browser-independent DOM test for animator-render.js. It runs the real render
// functions against realistic /meta and /snapshot payloads using a tiny DOM
// shim (no jsdom dependency), covering the field-mapping regression class,
// every problem-cell state, empty standings, hostile labels, long names, and
// the timer projection. Executed by tests/animator/test_render_js.py via Node.

"use strict";

const assert = require("assert");
const path = require("path");

const render = require(
  path.join(__dirname, "..", "..", "..", "animator", "static", "js", "animator-render.js"),
);

// ── Minimal DOM shim ────────────────────────────────────────────────────────
class El {
  constructor(tag) {
    this.tagName = tag;
    this.attributes = {};
    this.childNodes = [];
    this._text = "";
    this.classList = {
      _list: [],
      add: (c) => {
        if (this.classList._list.indexOf(c) === -1) {
          this.classList._list.push(c);
        }
      },
      remove: (c) => {
        this.classList._list = this.classList._list.filter((x) => x !== c);
      },
      contains: (c) => this.classList._list.indexOf(c) !== -1,
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
  appendChild(node) {
    // Match DOM move semantics: re-appending an existing child relocates it to
    // the end rather than duplicating it (relied on by keyed reconciliation).
    const i = this.childNodes.indexOf(node);
    if (i !== -1) {
      this.childNodes.splice(i, 1);
    }
    this.childNodes.push(node);
    return node;
  }
  removeChild(node) {
    this.childNodes = this.childNodes.filter((n) => n !== node);
    return node;
  }
  replaceChildren() {
    this.childNodes = [];
  }
  get children() {
    return this.childNodes;
  }
  get lastChild() {
    return this.childNodes[this.childNodes.length - 1];
  }
  set textContent(value) {
    this._text = String(value);
    this.childNodes = [];
  }
  get textContent() {
    if (this.childNodes.length === 0) {
      return this._text;
    }
    return this._text + this.childNodes.map((n) => n.textContent).join("");
  }
  get className() {
    return this.getAttribute("class") || "";
  }
  hasClass(c) {
    return this.classList.contains(c) || this.className.split(/\s+/).indexOf(c) !== -1;
  }
}

const doc = {
  createElement: (tag) => new El(tag),
  createElementNS: (_ns, tag) => new El(tag),
};

// The asset mount bases the page passes to extractProblems (data-*-base attrs).
const ASSETS = { balloonBase: "/assets/balloon" };

// ── released final detection: ended + explicitly unfrozen snapshot ─────────
(function testReleasedFinalDetection() {
  const meta = { end_time: "2026-06-20T16:00:00Z" };
  const afterEnd = Date.parse("2026-06-20T16:00:01Z");
  const beforeEnd = Date.parse("2026-06-20T15:59:59Z");

  assert.strictEqual(
    render.isReleasedFinal(meta, { is_frozen: false }, afterEnd),
    true,
  );
  assert.strictEqual(
    render.isReleasedFinal(meta, { is_frozen: true }, afterEnd),
    false,
    "an ended but unreleased scoreboard stays frozen",
  );
  assert.strictEqual(
    render.isReleasedFinal(meta, { is_frozen: false }, beforeEnd),
    false,
    "a release flag cannot make a running contest final",
  );
  assert.strictEqual(
    render.isReleasedFinal({ end_time: "invalid" }, { is_frozen: false }, afterEnd),
    false,
  );
})();

function makeHeader() {
  const header = new El("tr");
  ["#", "Team", "Solved", "Time"].forEach((label) => {
    const th = new El("th");
    th.textContent = label;
    header.appendChild(th);
  });
  return header;
}

function findByTag(node, tag) {
  const out = [];
  (function walk(n) {
    if (n.tagName === tag) {
      out.push(n);
    }
    (n.childNodes || []).forEach(walk);
  })(node);
  return out;
}

// ── extractProblems: no [object Object] ─────────────────────────────────────
(function testExtractProblems() {
  const meta = {
    problems: [
      { problem_id: "p1", ordinal: 1, label: "A", balloon_color: "ff0000" },
      { problem_id: "p2", ordinal: 2, label: "B", balloon_color: "00ff00" },
    ],
  };
  const problems = render.extractProblems(meta, ASSETS);
  assert.deepStrictEqual(
    problems.map((p) => p.label),
    ["A", "B"],
  );
  assert.strictEqual(problems[0].problemId, "p1");
  assert.strictEqual(problems[0].color, "ff0000");
  assert.strictEqual(problems[0].balloonBase, "/assets/balloon");
  problems.forEach((p) => assert.notStrictEqual(p.label, "[object Object]"));
})();

// ── renderHeader: balloon <img> src + alt + data hook ───────────────────────
(function testRenderHeader() {
  const header = makeHeader();
  const problems = render.extractProblems(
    { problems: [{ problem_id: "p1", ordinal: 1, label: "A", balloon_color: "ff0000" }] },
    ASSETS,
  );
  render.renderHeader(doc, header, problems);
  assert.strictEqual(header.children.length, 5); // 4 fixed + 1 problem
  const th = header.lastChild;
  assert.strictEqual(th.getAttribute("data-problem-id"), "p1");
  const imgs = findByTag(th, "img");
  assert.strictEqual(imgs.length, 1);
  assert.strictEqual(imgs[0].getAttribute("src"), "/assets/balloon/ff0000/A");
  assert.strictEqual(imgs[0].getAttribute("alt"), "Problem A");
  // Re-render must not accumulate columns.
  render.renderHeader(doc, header, problems);
  assert.strictEqual(header.children.length, 5);
})();

// ── createBalloonImage: invalid color / missing base omit the src ───────────
(function testBalloonImageFallback() {
  const bad = render.createBalloonImage(doc, { color: "not-a-color", label: "A", balloonBase: "/assets/balloon" });
  assert.strictEqual(bad.tagName, "img");
  assert.strictEqual(bad.getAttribute("src"), null, "invalid color -> no src");

  const noBase = render.createBalloonImage(doc, { color: "ff0000", label: "A", balloonBase: null });
  assert.strictEqual(noBase.getAttribute("src"), null, "missing base -> no src");

  const ok = render.createBalloonImage(doc, { color: "ff0000", label: "A", balloonBase: "/assets/balloon" });
  assert.strictEqual(ok.getAttribute("src"), "/assets/balloon/ff0000/A");
})();

// ── Problem-cell states + label-keyed lookup (the P1 regression guard) ───────
(function testProblemCellStates() {
  const problems = render.extractProblems({
    problems: [{ problem_id: "p1", ordinal: 1, label: "A", balloon_color: "ff0000" }],
  });

  const solved = render.buildRow(doc, problems, {
    rank: 1,
    team_id: "t1",
    team_name: "Team",
    team_fullname: "Team",
    problems_solved: 1,
    total_time: 20,
    problems: {
      A: {
        solved: true,
        attempts: 2,
        solved_at_minutes: 15,
        penalty: 40,
        is_first_balloon: true,
      },
    },
  });
  // The cell must reflect the solved state, keyed by label "A" — not render as
  // unattempted (which is what the pre-fix [object Object] key produced).
  const solvedCell = solved.childNodes[4];
  assert.ok(solvedCell.hasClass("animator-cell--solved"), "solved cell class");
  assert.ok(solvedCell.hasClass("animator-cell--first"), "first-balloon class");
  assert.ok(solvedCell.textContent.indexOf("+2 (40')") !== -1, "attempt penalty before solve");
  assert.ok(solvedCell.textContent.indexOf("15'") !== -1, "solve time");
  assert.ok(solvedCell.textContent.indexOf("first solve") !== -1, "a11y first-solve text");
  assert.strictEqual(solvedCell.getAttribute("data-problem-id"), "p1");
  // Row-level mapping uses problems_solved / total_time.
  assert.strictEqual(solved.getAttribute("data-team-id"), "t1");
  assert.strictEqual(solved.childNodes[2].textContent, "1"); // solved count
  assert.strictEqual(solved.childNodes[3].textContent, "20"); // penalty

  const pending = render.buildProblemCell(doc, problems[0], {
    solved: false,
    attempts: 1,
    penalty: 20,
    is_pending: true,
  });
  assert.ok(pending.hasClass("animator-cell--pending"));
  assert.ok(pending.textContent.indexOf("? −1") !== -1);
  assert.ok(pending.textContent.indexOf("(20')") !== -1);
  assert.ok(pending.textContent.indexOf("pending") !== -1);

  const attempted = render.buildProblemCell(doc, problems[0], {
    solved: false,
    attempts: 3,
    penalty: 60,
    is_pending: false,
  });
  assert.ok(attempted.hasClass("animator-cell--attempted"));
  assert.ok(attempted.textContent.indexOf("−3") !== -1);
  assert.ok(attempted.textContent.indexOf("(60')") !== -1);
  assert.ok(attempted.textContent.indexOf("failed attempts") !== -1);

  const none = render.buildProblemCell(doc, problems[0], undefined);
  assert.ok(none.textContent.indexOf("no attempts") !== -1);
  assert.ok(!none.hasClass("animator-cell--solved"));
})();

// ── Empty standings ─────────────────────────────────────────────────────────
(function testEmptyStandings() {
  const tbody = new El("tbody");
  const hasRows = render.renderStandings(doc, tbody, [], []);
  assert.strictEqual(hasRows, false);
  assert.strictEqual(tbody.childNodes.length, 0);
})();

// ── Hostile label stays inert: no letter segment, alt is an attribute ────────
(function testHostileLabel() {
  const header = makeHeader();
  const hostile = "<img src=x onerror=alert(1)>";
  render.renderHeader(doc, header, [
    { label: hostile, color: "ff0000", problemId: "p1", balloonBase: "/assets/balloon" },
  ]);
  const img = findByTag(header.lastChild, "img")[0];
  // The label does not start with an ASCII letter, so it is never placed into
  // the URL path: the src stays color-only. The label is carried only in alt,
  // set via setAttribute (an attribute, never parsed as markup).
  assert.strictEqual(img.getAttribute("src"), "/assets/balloon/ff0000");
  assert.strictEqual(img.getAttribute("alt"), "Problem " + hostile);
})();

// ── Long team name still renders ────────────────────────────────────────────
(function testLongName() {
  const longName = "X".repeat(200);
  const row = render.buildRow(doc, [], {
    rank: 1,
    team_id: "t1",
    team_name: longName,
    team_fullname: longName,
    problems_solved: 0,
    total_time: 0,
    problems: {},
  });
  assert.ok(row.textContent.indexOf(longName) !== -1);
})();

// ── Team cell: modal trigger on the first line, site on the second ───────────
(function testTeamCellOrder() {
  const row = render.buildRow(doc, [], {
    rank: 1,
    team_id: "t1",
    team_name: "team01", // login id
    team_fullname: "Ada Lovelace", // full name
    site_name: "Campus Centro",
    problems_solved: 0,
    total_time: 0,
    problems: {},
  });
  const th = row.childNodes[1];
  const buttons = findByTag(th, "button");
  const spans = findByTag(th, "span");
  assert.strictEqual(buttons.length, 1);
  assert.strictEqual(buttons[0].getAttribute("class"), "team-media-trigger animator-team-primary");
  assert.strictEqual(buttons[0].getAttribute("data-bs-toggle"), "modal");
  assert.strictEqual(buttons[0].getAttribute("data-bs-target"), "#team-media-modal");
  assert.strictEqual(buttons[0].getAttribute("data-team-id"), "t1");
  assert.strictEqual(buttons[0].getAttribute("title"), "Ada Lovelace");
  assert.strictEqual(buttons[0].textContent, "Ada Lovelace", "the trigger carries the full name");
  assert.strictEqual(spans[0].getAttribute("class"), "animator-team-secondary");
  assert.strictEqual(spans[0].textContent, "Campus Centro", "the second line is the site");

  // No full name or site: the login id is the single primary line.
  const row2 = render.buildRow(doc, [], {
    rank: 1,
    team_id: "t2",
    team_name: "team02",
    team_fullname: "team02",
    problems_solved: 0,
    total_time: 0,
    problems: {},
  });
  const buttons2 = findByTag(row2.childNodes[1], "button");
  const spans2 = findByTag(row2.childNodes[1], "span");
  assert.strictEqual(buttons2[0].textContent, "team02");
  assert.strictEqual(spans2.length, 0, "no second line when the site is absent");

  const hostile = '<img src=x onerror="alert(1)">';
  const hostileRow = render.buildRow(doc, [], {
    rank: 1,
    team_id: "hostile",
    team_name: hostile,
    team_fullname: hostile,
    problems_solved: 0,
    total_time: 0,
    problems: {},
  });
  const hostileButton = findByTag(hostileRow.childNodes[1], "button")[0];
  assert.strictEqual(hostileButton.textContent, hostile);
  assert.strictEqual(hostileButton.getAttribute("title"), hostile);
})();

// ── Timer projection: all four states + invalid ─────────────────────────────
(function testTimerView() {
  const start = 1000000;
  const end = start + 5 * 3600 * 1000;

  const before = render.computeTimerView(start, end, false, start - 60000);
  assert.strictEqual(before.state, "scheduled");
  assert.strictEqual(before.running, true);

  const during = render.computeTimerView(start, end, false, start + 3600000);
  assert.strictEqual(during.state, "running");
  assert.strictEqual(during.text, "01:00:00");
  assert.strictEqual(during.running, true);

  const frozen = render.computeTimerView(start, end, true, start + 3600000);
  assert.strictEqual(frozen.state, "frozen");
  assert.strictEqual(frozen.label, "Frozen");
  assert.strictEqual(frozen.ended, false);

  const frozenEnded = render.computeTimerView(start, end, true, end + 1);
  assert.strictEqual(frozenEnded.state, "frozen");
  assert.strictEqual(frozenEnded.label, "Frozen");
  assert.strictEqual(frozenEnded.ended, true);
  assert.strictEqual(frozenEnded.text, "05:00:00");
  assert.strictEqual(frozenEnded.running, false);

  const finalEnded = render.computeTimerView(start, end, false, end + 1);
  assert.strictEqual(finalEnded.state, "final");
  assert.strictEqual(finalEnded.label, "Final");
  assert.strictEqual(finalEnded.ended, true);
  assert.strictEqual(finalEnded.text, "05:00:00");
  assert.strictEqual(finalEnded.running, false);

  const invalid = render.computeTimerView(null, NaN, false, Date.now());
  assert.strictEqual(invalid.state, "unknown");
  assert.strictEqual(invalid.running, false);
})();

// ── The Frozen pill says how much of the board is hidden ────────────────────
(function testFrozenLabelNamesTheHiddenWindow() {
  const start = 1000000;
  const end = start + 5 * 3600 * 1000;
  const freeze = start + 4 * 3600 * 1000; // one hour of freeze by the rules

  // While the contest runs, the window is measured to NOW: at 04:35 on a board
  // frozen at 04:00 only 35 minutes are hidden, not the 60 the rules set aside.
  const running = render.computeTimerView(start, end, true, freeze + 35 * 60000, freeze);
  assert.strictEqual(running.label, "Frozen · last 35 min hidden");
  assert.strictEqual(running.state, "frozen");

  // Once ended it is measured to the END -- now the full planned window is hidden.
  const ended = render.computeTimerView(start, end, true, end + 999999, freeze);
  assert.strictEqual(ended.label, "Frozen · last 1 h hidden");
  assert.strictEqual(ended.ended, true);

  // A released final board is not frozen and says nothing about it.
  assert.strictEqual(render.computeTimerView(start, end, false, end + 1, freeze).label, "Final");

  // Wording a person would use, not a duration clock.
  assert.strictEqual(render.formatHiddenWindow(45 * 60000), "45 min");
  assert.strictEqual(render.formatHiddenWindow(60 * 60000), "1 h");
  assert.strictEqual(render.formatHiddenWindow(80 * 60000), "1 h 20 min");
  assert.strictEqual(render.formatHiddenWindow(2 * 3600 * 1000), "2 h");

  // Unknown, nonsensical, or sub-minute windows degrade to a bare "Frozen"
  // rather than showing a number a room would read as authoritative.
  assert.strictEqual(render.frozenLabel(start, end, null, end), "Frozen");
  assert.strictEqual(render.frozenLabel(start, end, undefined, end), "Frozen");
  assert.strictEqual(render.frozenLabel(start, end, NaN, end), "Frozen");
  assert.strictEqual(render.frozenLabel(start, NaN, freeze, end), "Frozen", "an unknown end hides nothing knowable");
  assert.strictEqual(
    render.frozenLabel(start, end, start - 1, end),
    "Frozen",
    "a freeze before the start is not a time",
  );
  assert.strictEqual(
    render.frozenLabel(start, end, freeze, freeze + 30000),
    "Frozen",
    "under a minute hidden is not worth a number",
  );
  assert.strictEqual(render.computeTimerView(start, end, true, end + 1).label, "Frozen");

  // A contest frozen from its own start has hidden all of itself.
  assert.strictEqual(render.frozenLabel(start, end, start, end), "Frozen · last 5 h hidden");
})();

// ── Only a first solve carries artwork; the balloon lives in the header ──────
(function testFirstSolverCellKeepsItsStarAndOrdinarySolvesDropTheBalloon() {
  const problems = render.extractProblems(
    { problems: [{ problem_id: "p1", ordinal: 1, label: "A", balloon_color: "ff0000" }] },
    ASSETS,
  );
  const first = render.buildProblemCell(doc, problems[0], {
    problem_id: "p1",
    solved: true,
    attempts: 0,
    solved_at_minutes: 12,
    penalty: 0,
    is_first_balloon: true,
  });
  const marks = findByTag(first, "span").filter(
    (node) => (node.getAttribute("class") || "").indexOf("noca-cell-first-mark") !== -1,
  );
  assert.strictEqual(marks.length, 1, "first-solver cell shows a star");
  assert.strictEqual(marks[0].textContent, "\u2605", "the star is a glyph, not served artwork");
  assert.ok(marks[0].getAttribute("class").indexOf("animator-cell-star") !== -1, "star cell class");
  assert.ok(
    marks[0].getAttribute("class").indexOf("animator-balloon") === -1,
    "the in-cell star must not take the header balloon's class, which pins a header height",
  );
  assert.strictEqual(marks[0].getAttribute("aria-hidden"), "true", "the star is decorative");
  assert.strictEqual(
    findByTag(first, "img").length,
    0,
    "the star is coloured from the per-column stylesheet, so it costs no request",
  );
  assert.ok(
    first.textContent.indexOf("first solve") !== -1,
    "the visually-hidden span is the star's single accessible announcement",
  );
  assert.ok(first.hasClass("animator-cell--first"));
  assert.ok(first.textContent.indexOf("12'") !== -1);
  assert.ok(first.textContent.indexOf("+") === -1, "first-attempt solve has no solitary plus");
  // An ordinary solve carries no artwork at all: the problem's balloon identifies
  // its column from the header, and repeating it per cell is what made rows 80px tall.
  const plain = render.buildProblemCell(doc, problems[0], {
    problem_id: "p1",
    solved: true,
    attempts: 0,
    solved_at_minutes: 15,
    penalty: 0,
    is_first_balloon: false,
  });
  assert.strictEqual(
    findByTag(plain, "img").length,
    0,
    "an ordinary solve carries no in-cell artwork; the balloon lives in the column header",
  );
  assert.ok(plain.textContent.indexOf("15'") !== -1, "the solve minute still reads");
})();

// ── Keyed reconciliation preserves identity, order, removal, and transients ──
(function testReconciliation() {
  const tbody = new El("tbody");
  const problems = render.extractProblems({
    problems: [{ problem_id: "p1", ordinal: 1, label: "A", balloon_color: "ff0000" }],
  });
  const cell = (over) => Object.assign({ problem_id: "p1", solved: false, attempts: 0 }, over);
  const row = (rank, id, extra) =>
    Object.assign(
      { rank: rank, team_id: id, team_name: id, team_fullname: id, problems_solved: 0, total_time: 0, problems: {} },
      extra,
    );

  render.renderStandings(doc, tbody, problems, [
    row(1, "t1", { problems_solved: 1, total_time: 10, problems: { A: cell({ solved: true }) } }),
    row(2, "t2"),
  ]);
  assert.strictEqual(tbody.children.length, 2);
  const t1First = tbody.children[0];
  assert.strictEqual(t1First.getAttribute("data-team-id"), "t1");
  const t1Trigger = findByTag(t1First.children[1], "button")[0];
  // A transient flash a full rebuild would drop; reconciliation must keep it.
  t1First.children[4].classList.add("animator-cell--flash-solved");

  // t2 overtakes t1, a new t3 appears.
  render.renderStandings(doc, tbody, problems, [
    row(1, "t2", { problems_solved: 1, total_time: 5, problems: { A: cell({ solved: true }) } }),
    row(2, "t1", {
      team_fullname: "Team One Renamed",
      site_name: "Campus Norte",
      problems_solved: 1,
      total_time: 10,
      problems: { A: cell({ solved: true }) },
    }),
    row(3, "t3"),
  ]);
  assert.deepStrictEqual(
    tbody.children.map((r) => r.getAttribute("data-team-id")),
    ["t2", "t1", "t3"],
    "rows reordered into authoritative server order",
  );
  const t1Second = tbody.children[1];
  assert.strictEqual(t1Second, t1First, "surviving row keeps element identity");
  const refreshedTrigger = findByTag(t1Second.children[1], "button")[0];
  assert.strictEqual(refreshedTrigger, t1Trigger, "Bootstrap relatedTarget survives a live refresh");
  assert.strictEqual(refreshedTrigger.textContent, "Team One Renamed");
  assert.strictEqual(refreshedTrigger.getAttribute("title"), "Team One Renamed");
  assert.strictEqual(findByTag(t1Second.children[1], "span")[0].textContent, "Campus Norte");
  assert.ok(t1Second.children[4].hasClass("animator-cell--flash-solved"), "transient class survives re-render");
  assert.strictEqual(t1Second.children[0].textContent, "2", "rank updated in place");

  // t2 and t3 drop out; only t1 remains.
  render.renderStandings(doc, tbody, problems, [row(1, "t1")]);
  assert.strictEqual(tbody.children.length, 1);
  assert.strictEqual(tbody.children[0].getAttribute("data-team-id"), "t1", "departed teams removed");
})();

// ── Medals on the live board ────────────────────────────────────────────────
// The same primitives drive the reveal ceremony, so what is asserted here is the
// board *using* them: the watermark in the team cell, `data-medal` on the row,
// the band-end rule, and — the case a set-only implementation gets wrong —
// dropping all three when a team falls off the podium.
(function testMedals() {
  const tbody = new El("tbody");
  const problems = [];
  const row = (rank, id, medal) => ({
    rank: rank,
    team_id: id,
    team_name: id,
    team_fullname: id,
    problems_solved: 0,
    total_time: 0,
    problems: {},
    medal: medal,
  });
  const options = { medalBase: "/assets/medal" };

  render.renderStandings(
    doc,
    tbody,
    problems,
    [row(1, "t1", "gold"), row(2, "t2", "silver"), row(3, "t3", "silver"), row(4, "t4", null)],
    options,
  );

  assert.deepStrictEqual(
    tbody.children.map((r) => r.getAttribute("data-medal")),
    ["gold", "silver", "silver", null],
  );
  // Only the last row of each band closes it.
  assert.deepStrictEqual(
    tbody.children.map((r) => r.hasClass("animator-row--band-end")),
    [true, false, true, false],
  );

  const watermark = tbody.children[0].children[1].children.at(-1);
  assert.strictEqual(watermark.tagName, "img");
  assert.strictEqual(watermark.getAttribute("class"), "animator-medal-watermark");
  assert.strictEqual(watermark.getAttribute("src"), "/assets/medal/gold");
  assert.strictEqual(watermark.getAttribute("alt"), "gold medal");
  assert.strictEqual(
    render.createMedalImage(doc, "/assets/medal", "platinum"),
    null,
    "an unknown band cannot create an asset URL",
  );
  assert.ok(
    tbody.children[3].children[1].children.every((child) => child.tagName !== "img"),
    "an unmedalled team has no watermark",
  );

  // t1 falls off the podium: the attribute, the rule, and the artwork all go.
  const t1 = tbody.children[0];
  render.renderStandings(
    doc,
    tbody,
    problems,
    [row(1, "t2", "gold"), row(2, "t3", null), row(3, "t1", null), row(4, "t4", null)],
    options,
  );
  const t1Again = tbody.children[2];
  assert.strictEqual(t1Again, t1, "surviving row keeps element identity");
  assert.strictEqual(t1Again.getAttribute("data-medal"), null, "a stale band is removed, not kept");
  assert.strictEqual(t1Again.hasClass("animator-row--band-end"), false);
  assert.ok(
    t1Again.children[1].children.every((child) => child.tagName !== "img"),
    "the watermark is gone once the medal is",
  );

  // Without a medal base the <img> is omitted rather than pointing nowhere.
  const bare = new El("tbody");
  render.renderStandings(doc, bare, problems, [row(1, "t1", "gold")], {});
  assert.ok(
    bare.children[0].children[1].children.every((child) => child.tagName !== "img"),
    "a missing medal base cannot render a source-less image",
  );

  const invalid = new El("tbody");
  render.renderStandings(doc, invalid, problems, [row(1, "t1", "platinum")], options);
  assert.strictEqual(invalid.children[0].getAttribute("data-medal"), null);
  assert.ok(
    invalid.children[0].children[1].children.every((child) => child.tagName !== "img"),
    "an unknown band cannot render a watermark",
  );
})();

// ── Per-column balloon colours reach the cells as one validated stylesheet ──
(function testProblemColorRules() {
  const problems = render.extractProblems(
    {
      problems: [
        { problem_id: "p1", ordinal: 1, label: "A", balloon_color: "ff0000" },
        { problem_id: "p2", ordinal: 2, label: "B", balloon_color: "00ff00" },
      ],
    },
    ASSETS,
  );
  const css = render.problemColorRules(problems);
  // Four fixed columns precede the problems, so problem 1 is the fifth cell.
  assert.ok(css.indexOf(".animator-cell:nth-child(5){--noca-cell-balloon:#ff0000}") !== -1);
  assert.ok(css.indexOf(".animator-cell:nth-child(6){--noca-cell-balloon:#00ff00}") !== -1);

  // A stylesheet executes whatever it is handed, so anything that is not a hex
  // colour is dropped rather than emitted. That column simply shows no colour.
  const hostile = render.problemColorRules([
    { label: "A", color: "red;} body{display:none}" },
    { label: "B", color: null },
    { label: "C", color: "00ff00" },
  ]);
  assert.strictEqual(hostile.indexOf("display:none"), -1, "no CSS injection through a stored colour");
  assert.strictEqual(hostile.indexOf("nth-child(5)"), -1, "an invalid colour emits no rule");
  assert.strictEqual(hostile.indexOf("nth-child(6)"), -1, "a missing colour emits no rule");
  assert.ok(hostile.indexOf("nth-child(7){--noca-cell-balloon:#00ff00}") !== -1, "valid siblings still emit");

  // The sheet is created by the renderer, not shipped in the page: both animator
  // templates assert the served HTML carries nothing inline.
  const head = new El("head");
  const sheetDoc = {
    head: head,
    createElement: (tag) => new El(tag),
    getElementById: (id) =>
      head.childNodes.filter((n) => n.getAttribute("id") === id)[0] || null,
  };
  const sheet = render.renderProblemColors(sheetDoc, problems);
  assert.strictEqual(sheet.tagName, "style");
  assert.strictEqual(head.childNodes.length, 1, "the sheet is appended once");
  assert.ok(sheet.textContent.indexOf("nth-child(6)") !== -1);

  // Replaced wholesale, never appended to, so a problem set that shrinks cannot
  // leave a departed column still coloured -- and no second sheet appears.
  render.renderProblemColors(sheetDoc, problems.slice(0, 1));
  assert.strictEqual(head.childNodes.length, 1, "the sheet is reused, not duplicated");
  assert.strictEqual(sheet.textContent.indexOf("nth-child(6)"), -1, "stale columns are dropped");
})();

// ── Team cell: the never-signed-in marker ───────────────────────────────────
(function testAbsentTeamMarker() {
  const absentStanding = {
    rank: 1,
    team_id: "t9",
    team_name: "team09",
    team_fullname: "Team Nine",
    problems_solved: 0,
    total_time: 0,
    problems: {},
    absent: true,
  };
  const row = render.buildRow(doc, [], absentStanding);
  const th = row.childNodes[1];
  assert.strictEqual(th.getAttribute("data-team-absent"), "");
  const icons = findByTag(th, "i");
  assert.strictEqual(icons.length, 1, "exactly one marker glyph");
  assert.strictEqual(icons[0].textContent, "person_off");
  assert.strictEqual(icons[0].getAttribute("role"), "img");
  assert.ok(icons[0].getAttribute("aria-label").length > 0, "the glyph is announced, not silent");
  // Inside the trigger, so it sits on the name line rather than a line of its own.
  const button = findByTag(th, "button")[0];
  assert.ok(button.childNodes.indexOf(icons[0]) !== -1);

  // A team that signs in loses both the attribute and the glyph on the next
  // refresh: the live board reconciles rows in place, so a stale marker would
  // otherwise survive for the life of the page.
  render.updateRow(doc, row, [], Object.assign({}, absentStanding, { absent: false }), {});
  assert.strictEqual(th.getAttribute("data-team-absent"), null);
  assert.strictEqual(findByTag(th, "i").length, 0);

  // A payload with no such field at all -- the ceremony's projection -- marks
  // nobody rather than throwing.
  const plain = render.buildRow(doc, [], {
    rank: 2,
    team_id: "t10",
    team_name: "team10",
    team_fullname: "Team Ten",
    problems_solved: 0,
    total_time: 0,
    problems: {},
  });
  assert.strictEqual(plain.childNodes[1].getAttribute("data-team-absent"), null);
  assert.strictEqual(findByTag(plain.childNodes[1], "i").length, 0);
})();

console.log("animator-render DOM contract: all assertions passed");
