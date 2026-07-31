//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
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
  assert.strictEqual(list.children[0].textContent, "42\u2032alpha submitted A");
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
  assert.strictEqual(ordinary.list.children.length, 0, "verdict waits for its snapshot");
  ordinary.rail.reconcile(snapshot(), snapshot());
  assert.strictEqual(ordinary.list.children[0].textContent, "42\u2032alpha got WA for A");

  const balloon = makeRail();
  balloon.rail.observeVerdict(verdict("j-ac"));
  balloon.rail.reconcile(snapshot(), snapshot({ solved: true }));
  assert.strictEqual(balloon.list.children[0].textContent, "42\u2032alpha got balloon for A");

  const first = makeRail();
  first.rail.observeVerdict(verdict("j-first"));
  first.rail.reconcile(snapshot(), snapshot({ solved: true, first: true }));
  assert.strictEqual(first.list.children[0].textContent, "42\u2032alpha is first solver for A");
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
      "42\u2032alpha got WA for A",
      "42\u2032alpha is first solver for A",
      "42\u2032alpha got WA for A",
    ],
  );
})();

(function testSlidingWindowAndMissingMappings() {
  const { rail, list } = makeRail();
  rail.observeSubmission({ submission_id: "missing", team_id: "unknown", problem_id: "p1" });
  assert.strictEqual(list.children.length, 0, "unmappable events do not expose raw ids");

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

console.log("animator events contract: all assertions passed");
