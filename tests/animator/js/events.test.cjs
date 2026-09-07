//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Browser-independent contract for the session-local Animator event rail.
"use strict";

const assert = require("assert");
const path = require("path");
const events = require(
  path.join(__dirname, "..", "..", "..", "animator", "static", "js", "animator-events.js"),
);

class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.attributes = {};
    this.children = [];
    this._text = "";
    this.scrollLeft = 0;
    this.scrollWidth = 400;
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
  removeAttribute(name) {
    delete this.attributes[name];
  }
  get hidden() {
    return Object.prototype.hasOwnProperty.call(this.attributes, "hidden");
  }
  get firstChild() {
    return this.children[0] || null;
  }
  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }
  get textContent() {
    if (this.children.length) {
      return this.children.map((child) => child.textContent).join("");
    }
    return this._text;
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) {
      this.children.splice(index, 1);
    }
    return child;
  }
}

const doc = {
  createElement(tag) {
    return new El(tag);
  },
};

function snapshot({ solved = false, first = false } = {}) {
  return {
    standings: [
      {
        team_id: "t1",
        team_name: "alpha",
        problems: {
          A: {
            label: "A",
            problem_id: "p1",
            solved: solved,
            is_first_balloon: first,
          },
        },
      },
    ],
  };
}

function makeRail({ reduced = false } = {}) {
  let now = Date.parse("2026-07-24T12:00:00Z");
  const container = new El("section");
  const list = new El("ol");
  container.setAttribute("hidden", "");
  const rail = events.createEventRail({
    doc: doc,
    container: container,
    list: list,
    now: () => now,
    reducedMotion: { matches: reduced },
  });
  rail.configure({
    start_time: "2026-07-24T11:18:00Z",
    problems: [{ problem_id: "p1", label: "A" }],
  });
  rail.reconcile(null, snapshot());
  return {
    rail: rail,
    container: container,
    list: list,
    setNow(value) {
      now = value;
    },
  };
}

// The rail is revealed with the board, so its list holds a non-event
// placeholder until the first real event lands. Assertions about events count
// only the rows that carry an event key.
function eventItems(list) {
  return list.children.filter((item) =>
    Object.prototype.hasOwnProperty.call(item.attributes, "data-event-key"),
  );
}

function verdict(id, code = "AC") {
  return {
    judgment_id: id,
    team_id: "t1",
    problem_id: "p1",
    verdict: code,
  };
}

(function testSubmissionMinuteDedupAndTextSafety() {
  const { rail, container, list } = makeRail({ reduced: true });
  rail.observeSubmission({ submission_id: "s1", team_id: "t1", problem_id: "p1" });
  assert.strictEqual(container.hidden, false);
  assert.strictEqual(list.children.length, 1);
  assert.strictEqual(list.children[0].textContent, "00:42alpha submitted A");
  assert.strictEqual(list.attributes["data-pace"], "short");
  assert.strictEqual(container.scrollLeft, container.scrollWidth);

  rail.observeSubmission({ submission_id: "s1", team_id: "t1", problem_id: "p1" });
  assert.strictEqual(list.children.length, 1, "duplicate submission is ignored");

  const hostile = makeRail();
  hostile.rail.reconcile(null, {
    standings: [
      {
        team_id: "evil",
        team_name: "<img src=x onerror=alert(1)>",
        problems: { A: { label: "A", problem_id: "p1" } },
      },
    ],
  });
  hostile.rail.observeSubmission({ submission_id: "s2", team_id: "evil", problem_id: "p1" });
  assert.strictEqual(hostile.list.children[0].children.length, 2);
  assert.ok(hostile.list.children[0].textContent.includes("<img src=x"));
})();

(function testVerdictMessagesWaitForSnapshotAndChooseSpecificResult() {
  const ordinary = makeRail();
  ordinary.rail.observeVerdict(verdict("j-wa", "WA"));
  assert.strictEqual(eventItems(ordinary.list).length, 0, "verdict waits for its snapshot");
  ordinary.rail.reconcile(snapshot(), snapshot());
  assert.strictEqual(ordinary.list.children[0].textContent, "00:42alpha got WA for A");

  const balloon = makeRail();
  balloon.rail.observeVerdict(verdict("j-ac"));
  balloon.rail.reconcile(snapshot(), snapshot({ solved: true }));
  assert.strictEqual(balloon.list.children[0].textContent, "00:42alpha got balloon for A");

  const first = makeRail();
  first.rail.observeVerdict(verdict("j-first"));
  first.rail.reconcile(snapshot(), snapshot({ solved: true, first: true }));
  assert.strictEqual(first.list.children[0].textContent, "00:42alpha is first solver for A");
})();

(function testBurstOrderingAndRedaction() {
  const { rail, list } = makeRail();
  rail.observeVerdict(verdict("j-wa", "WA"));
  rail.observeVerdict(verdict("j-ac", "AC"));
  rail.observeVerdict(verdict("j-after", "WA"));
  rail.observeVerdict({ redacted: true });
  assert.strictEqual(rail._pendingCount(), 3);
  rail.reconcile(snapshot(), snapshot({ solved: true, first: true }));
  assert.deepStrictEqual(
    list.children.map((item) => item.textContent),
    [
      "00:42alpha got WA for A",
      "00:42alpha is first solver for A",
      "00:42alpha got WA for A",
    ],
  );
})();

(function testSlidingWindowAndMissingMappings() {
  const { rail, list } = makeRail();
  rail.observeSubmission({ submission_id: "missing", team_id: "unknown", problem_id: "p1" });
  assert.strictEqual(eventItems(list).length, 0, "unmappable events do not expose raw ids");

  for (let index = 1; index <= 31; index += 1) {
    rail.observeSubmission({ submission_id: "s" + index, team_id: "t1", problem_id: "p1" });
  }
  assert.strictEqual(list.children.length, events.MAX_EVENTS);
  assert.strictEqual(rail._entries().length, events.MAX_EVENTS);
  assert.strictEqual(list.children[0].attributes["data-event-key"], "submission:s2");
  assert.strictEqual(list.attributes["data-pace"], "long");

  const pending = makeRail();
  for (let index = 1; index <= 31; index += 1) {
    pending.rail.observeVerdict(verdict("j" + index, "WA"));
  }
  assert.strictEqual(pending.rail._pendingCount(), events.MAX_EVENTS);
})();

(function testRailAppearsWithTheBoardAndParksAPlaceholder() {
  const container = new El("section");
  const list = new El("ol");
  container.setAttribute("hidden", "");
  const rail = events.createEventRail({
    doc: doc,
    container: container,
    list: list,
    now: () => Date.parse("2026-07-24T12:00:00Z"),
    reducedMotion: { matches: false },
  });
  rail.configure({
    start_time: "2026-07-24T11:18:00Z",
    problems: [{ problem_id: "p1", label: "A" }],
  });
  assert.strictEqual(container.hidden, true, "rail stays hidden until a snapshot arrives");

  rail.reconcile(null, snapshot());
  assert.strictEqual(container.hidden, false, "the first snapshot reveals the rail");
  assert.strictEqual(list.children.length, 1);
  assert.strictEqual(list.children[0].textContent, events.EMPTY_MESSAGE);
  assert.strictEqual(list.attributes["data-pace"], "idle", "the empty rail does not scroll");

  rail.reconcile(snapshot(), snapshot());
  assert.strictEqual(list.children.length, 1, "the placeholder is not duplicated");

  rail.observeSubmission({ submission_id: "s1", team_id: "t1", problem_id: "p1" });
  assert.deepStrictEqual(
    list.children.map((item) => item.textContent),
    ["00:42alpha submitted A"],
    "the first real event replaces the placeholder",
  );
  assert.strictEqual(list.attributes["data-pace"], "short");

  rail.reconcile(snapshot(), snapshot());
  assert.strictEqual(list.children.length, 1, "the placeholder does not come back");
})();

(function testSnapshotSeedIsAppliedOnceAndSharesLiveKeys() {
  const container = new El("section");
  const list = new El("ol");
  container.setAttribute("hidden", "");
  const rail = events.createEventRail({
    doc: doc,
    container: container,
    list: list,
    now: () => Date.parse("2026-07-24T12:00:00Z"),
    reducedMotion: { matches: false },
  });
  rail.configure({
    start_time: "2026-07-24T11:18:00Z",
    problems: [{ problem_id: "p1", label: "A" }],
  });

  const seeded = Object.assign(snapshot(), {
    recent_events: [
      { key: "submission:s9", minute: 3, kind: "submitted", team_name: "alpha", problem_label: "A" },
      {
        key: "verdict:j9",
        minute: 5,
        kind: "verdict",
        team_name: "alpha",
        problem_label: "A",
        verdict: "WA",
      },
      { key: "verdict:j10", minute: 7, kind: "first", team_name: "beta", problem_label: "B" },
      { key: "verdict:j11", minute: 9, kind: "balloon", team_name: "gamma", problem_label: "B" },
      { key: "verdict:bad", minute: 9, kind: "verdict", team_name: "", problem_label: "B" },
    ],
  });
  rail.reconcile(null, seeded);
  assert.deepStrictEqual(
    list.children.map((item) => item.textContent),
    [
      "00:03alpha submitted A",
      "00:05alpha got WA for A",
      "00:07beta is first solver for B",
      "00:09gamma got balloon for B",
    ],
    "the seed renders oldest first and drops unnameable entries",
  );
  assert.strictEqual(container.hidden, false);

  // A live event the seed already covered must not render twice.
  rail.observeVerdict(verdict("j9", "WA"));
  rail.reconcile(seeded, seeded);
  assert.strictEqual(list.children.length, 4, "a seeded verdict is not re-rendered live");

  // A second snapshot must not resurrect the backlog.
  rail.reconcile(seeded, seeded);
  assert.strictEqual(list.children.length, 4, "the seed is applied exactly once");

  rail.observeSubmission({ submission_id: "s-new", team_id: "t1", problem_id: "p1" });
  assert.strictEqual(list.children.length, 5, "live events still append after a seed");
})();

(function testLiveIndicatorTracksTheContestPhase() {
  const { rail, container } = makeRail();
  assert.ok(!("data-live" in container.attributes), "the rail claims nothing until told");
  rail.setLive(true);
  assert.strictEqual(container.attributes["data-live"], "true");
  rail.setLive(false);
  assert.strictEqual(container.attributes["data-live"], "false");
})();

(function testTeamsAreNamedByTheirFullName() {
  // A projector audience reads the team's name, never its login, so both the
  // seeded backlog and the live stream prefer `team_fullname`.
  const container = new El("section");
  const list = new El("ol");
  const rail = events.createEventRail({
    doc: doc,
    container: container,
    list: list,
    now: () => Date.parse("2026-07-24T12:00:00Z"),
    reducedMotion: { matches: false },
  });
  rail.configure({
    start_time: "2026-07-24T11:18:00Z",
    problems: [{ problem_id: "p1", label: "A" }],
  });
  const named = snapshot();
  named.standings[0].team_fullname = "Alpha Team";
  named.recent_events = [
    {
      key: "verdict:j1",
      minute: 2,
      kind: "first",
      team_name: "beta",
      team_fullname: "Beta Team",
      problem_label: "B",
    },
  ];
  rail.reconcile(null, named);
  rail.observeSubmission({ submission_id: "s1", team_id: "t1", problem_id: "p1" });
  assert.deepStrictEqual(
    list.children.map((item) => item.textContent),
    ["00:02Beta Team is first solver for B", "00:42Alpha Team submitted A"],
    "the rail names teams by their full name in both the seed and the live stream",
  );
})();

(function testFormatEventTime() {
  assert.strictEqual(events.formatEventTime(0), "00:00");
  assert.strictEqual(events.formatEventTime(3), "00:03");
  assert.strictEqual(events.formatEventTime(42), "00:42");
  assert.strictEqual(events.formatEventTime(59), "00:59");
  assert.strictEqual(events.formatEventTime(60), "01:00");
  assert.strictEqual(events.formatEventTime(137), "02:17");
  assert.strictEqual(events.formatEventTime(300), "05:00");
  assert.strictEqual(events.formatEventTime(-10), "00:00");
  assert.strictEqual(events.formatEventTime(NaN), "00:00");
  assert.strictEqual(events.formatEventTime(null), "00:00");
  assert.strictEqual(events.formatEventTime("42"), "00:42");
})();

console.log("animator events contract: all assertions passed");
