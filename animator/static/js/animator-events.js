//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Session-local recent-event rail for the Animator scoreboard. Submission
// events render immediately; verdicts wait for the paired authoritative
// snapshot so a solve can use the more specific balloon/first-solver wording.
// The rail appears with the board rather than with its first event: an empty
// rail carrying "No activity yet" tells a spectator the ticker is working and
// the contest is quiet, which a missing rail does not. The first post-start
// snapshot also seeds it with the server-built `recent_events` backlog, so a
// projector opened mid-contest starts with history instead of a blank strip.
// Exported as UMD: `window.AnimatorEvents` / `module.exports`.
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.AnimatorEvents = api;
  }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  var MAX_EVENTS = 30;
  var EMPTY_MESSAGE = "No activity yet";

  function getCell(snapshot, teamId, problemId) {
    var standings = snapshot && Array.isArray(snapshot.standings) ? snapshot.standings : [];
    for (var rowIndex = 0; rowIndex < standings.length; rowIndex += 1) {
      var row = standings[rowIndex];
      if (!row || String(row.team_id) !== String(teamId)) {
        continue;
      }
      var problems = row.problems || {};
      var labels = Object.keys(problems);
      for (var labelIndex = 0; labelIndex < labels.length; labelIndex += 1) {
        var cell = problems[labels[labelIndex]];
        if (cell && String(cell.problem_id) === String(problemId)) {
          return cell;
        }
      }
    }
    return null;
  }

  // The one place a rail sentence is written. Both the live path and the
  // server-supplied seed go through it, so a reloaded page and a page that
  // watched the same event happen read identically.
  function composeMessage(kind, teamName, problemLabel, verdict) {
    if (kind === "first") {
      return teamName + " is first solver for " + problemLabel;
    }
    if (kind === "balloon") {
      return teamName + " got balloon for " + problemLabel;
    }
    if (kind === "submitted") {
      return teamName + " submitted " + problemLabel;
    }
    return teamName + " got " + verdict + " for " + problemLabel;
  }

  function acceptedCandidate(events) {
    for (var index = 0; index < events.length; index += 1) {
      if (events[index].data.verdict === "AC") {
        return events[index].key;
      }
    }
    for (var fallback = 0; fallback < events.length; fallback += 1) {
      if (events[fallback].data.verdict === "PE") {
        return events[fallback].key;
      }
    }
    return null;
  }

  function createEventRail(options) {
    var doc = options.doc;
    var container = options.container;
    var list = options.list;
    var entries = [];
    var pendingVerdicts = [];
    var knownEvents = {};
    var teamNames = {};
    var problemLabels = {};
    var startMs = null;
    var placeholder = null;
    var seeded = false;

    function setVisible() {
      if (container) {
        container.removeAttribute("hidden");
      }
    }

    function currentMinute() {
      if (startMs === null) {
        return 0;
      }
      return Math.max(0, Math.floor((options.now() - startMs) / 60000));
    }

    // "idle" is not a speed: it parks the placeholder at rest so the empty
    // rail reads as a label rather than as a single item sliding by forever.
    function setPace() {
      if (!list) {
        return;
      }
      var pace = "idle";
      if (entries.length > 0) {
        pace = entries.length <= 5 ? "short" : entries.length <= 15 ? "medium" : "long";
      }
      list.setAttribute("data-pace", pace);
    }

    function clearPlaceholder() {
      if (placeholder && list) {
        list.removeChild(placeholder);
      }
      placeholder = null;
    }

    // Reveal the rail as soon as the board has something to show. The
    // placeholder is dropped by the first real event, so the trim in `append`
    // never has to reason about a non-event row sitting at the head of the list.
    function activate() {
      if (!list) {
        return;
      }
      setVisible();
      if (entries.length > 0 || placeholder) {
        return;
      }
      var item = doc.createElement("li");
      item.setAttribute("class", "animator-events-item animator-events-item--empty");
      var text = doc.createElement("span");
      text.setAttribute("class", "animator-events-text");
      text.textContent = EMPTY_MESSAGE;
      item.appendChild(text);
      list.appendChild(item);
      placeholder = item;
      setPace();
    }

    function append(minute, message, key) {
      if (!list || knownEvents[key]) {
        return;
      }
      knownEvents[key] = true;
      clearPlaceholder();
      entries.push({ key: key, minute: minute, message: message });
      var item = doc.createElement("li");
      item.setAttribute("class", "animator-events-item");
      item.setAttribute("data-event-key", key);
      var time = doc.createElement("span");
      time.setAttribute("class", "animator-events-time");
      time.textContent = String(minute) + "\u2032";
      var text = doc.createElement("span");
      text.setAttribute("class", "animator-events-text");
      text.textContent = message;
      item.appendChild(time);
      item.appendChild(text);
      list.appendChild(item);
      while (entries.length > MAX_EVENTS) {
        var removed = entries.shift();
        delete knownEvents[removed.key];
        list.removeChild(list.firstChild);
      }
      setPace();
      setVisible();
      if (options.reducedMotion && options.reducedMotion.matches && container) {
        container.scrollLeft = container.scrollWidth;
      }
    }

    // The dot beside the heading glows red only while the contest can still
    // produce activity. A scheduled or finished contest keeps the rail and the
    // marquee -- a contest with no freeze can be judged after the clock stops --
    // but the "live now" signal would be a lie, so it goes grey.
    function setLive(live) {
      if (container) {
        container.setAttribute("data-live", live ? "true" : "false");
      }
    }

    // Seed the rail from the snapshot's server-built backlog, once. Entries
    // arrive oldest first and carry the same keys the live stream uses, so an
    // event that is both seeded and streamed is rendered once. Re-seeding on a
    // later snapshot would resurrect entries the 30-item window had dropped, so
    // the first post-start snapshot is the only one that seeds.
    function seed(snapshot) {
      if (seeded) {
        return;
      }
      seeded = true;
      var items = snapshot && Array.isArray(snapshot.recent_events) ? snapshot.recent_events : [];
      items.forEach(function (item) {
        if (!item || !item.key || !item.team_name || !item.problem_label) {
          return;
        }
        var minute = Number(item.minute);
        append(
          isNaN(minute) ? 0 : minute,
          composeMessage(item.kind, String(item.team_name), String(item.problem_label), item.verdict),
          String(item.key),
        );
      });
    }

    function indexSnapshot(snapshot) {
      var standings = snapshot && Array.isArray(snapshot.standings) ? snapshot.standings : [];
      standings.forEach(function (row) {
        if (!row || row.team_id === undefined || row.team_id === null) {
          return;
        }
        teamNames[String(row.team_id)] = String(row.team_name || "");
        Object.keys(row.problems || {}).forEach(function (label) {
          var cell = row.problems[label];
          if (cell && cell.problem_id !== undefined && cell.problem_id !== null) {
            problemLabels[String(cell.problem_id)] = String(cell.label || label);
          }
        });
      });
    }

    function configure(meta) {
      var parsed = Date.parse(meta && meta.start_time);
      startMs = isNaN(parsed) ? null : parsed;
      var problems = meta && Array.isArray(meta.problems) ? meta.problems : [];
      problems.forEach(function (problem) {
        if (problem && problem.problem_id !== undefined && problem.problem_id !== null) {
          problemLabels[String(problem.problem_id)] = String(problem.label || "");
        }
      });
    }

    function observeSubmission(data) {
      if (!data || data.submission_id === undefined) {
        return;
      }
      var team = teamNames[String(data.team_id)] || "";
      var label = problemLabels[String(data.problem_id)] || "";
      if (!team || !label) {
        return;
      }
      append(
        currentMinute(),
        composeMessage("submitted", team, label, null),
        "submission:" + data.submission_id,
      );
    }

    function observeVerdict(data) {
      if (
        !data ||
        data.redacted ||
        data.judgment_id === undefined ||
        data.team_id === null ||
        data.team_id === undefined ||
        data.problem_id === null ||
        data.problem_id === undefined ||
        !data.verdict
      ) {
        return;
      }
      var key = "verdict:" + data.judgment_id;
      if (knownEvents[key]) {
        return;
      }
      knownEvents[key] = "pending";
      pendingVerdicts.push({ key: key, minute: currentMinute(), data: data });
      while (pendingVerdicts.length > MAX_EVENTS) {
        var discarded = pendingVerdicts.shift();
        delete knownEvents[discarded.key];
      }
    }

    function reconcile(previous, snapshot) {
      indexSnapshot(snapshot);
      seed(snapshot);
      activate();
      if (!previous || pendingVerdicts.length === 0) {
        return;
      }
      var byCell = {};
      pendingVerdicts.forEach(function (event) {
        var cellKey = String(event.data.team_id) + "\u0000" + String(event.data.problem_id);
        (byCell[cellKey] = byCell[cellKey] || []).push(event);
      });
      var specialByEvent = {};
      Object.keys(byCell).forEach(function (cellKey) {
        var events = byCell[cellKey];
        var data = events[0].data;
        var before = getCell(previous, data.team_id, data.problem_id);
        var after = getCell(snapshot, data.team_id, data.problem_id);
        if (!before || !after || before.solved || !after.solved) {
          return;
        }
        var candidate = acceptedCandidate(events);
        if (candidate) {
          specialByEvent[candidate] =
            !before.is_first_balloon && after.is_first_balloon ? "first" : "balloon";
        }
      });
      pendingVerdicts.forEach(function (event) {
        var data = event.data;
        var team = teamNames[String(data.team_id)] || "";
        var label = problemLabels[String(data.problem_id)] || "";
        if (!team || !label) {
          knownEvents[event.key] = true;
          return;
        }
        knownEvents[event.key] = false;
        var kind = specialByEvent[event.key] || "verdict";
        append(event.minute, composeMessage(kind, team, label, data.verdict), event.key);
      });
      pendingVerdicts = [];
    }

    return {
      activate: activate,
      configure: configure,
      seed: seed,
      setLive: setLive,
      observeSubmission: observeSubmission,
      observeVerdict: observeVerdict,
      reconcile: reconcile,
      _entries: function () {
        return entries.slice();
      },
      _pendingCount: function () {
        return pendingVerdicts.length;
      },
    };
  }

  return { MAX_EVENTS: MAX_EVENTS, EMPTY_MESSAGE: EMPTY_MESSAGE, createEventRail: createEventRail };
});
