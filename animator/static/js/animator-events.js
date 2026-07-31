//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Session-local recent-event rail for the Animator scoreboard. Submission
// events render immediately; verdicts wait for the paired authoritative
// snapshot so a solve can use the more specific balloon/first-solver wording.
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

    function setPace() {
      if (!list) {
        return;
      }
      var pace = entries.length <= 5 ? "short" : entries.length <= 15 ? "medium" : "long";
      list.setAttribute("data-pace", pace);
    }

    function append(minute, message, key) {
      if (!list || knownEvents[key]) {
        return;
      }
      knownEvents[key] = true;
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
      append(currentMinute(), team + " submitted " + label, "submission:" + data.submission_id);
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
        var kind = specialByEvent[event.key];
        var message;
        if (kind === "first") {
          message = team + " is first solver for " + label;
        } else if (kind === "balloon") {
          message = team + " got balloon for " + label;
        } else {
          message = team + " got " + data.verdict + " for " + label;
        }
        append(event.minute, message, event.key);
      });
      pendingVerdicts = [];
    }

    return {
      configure: configure,
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

  return { MAX_EVENTS: MAX_EVENTS, createEventRail: createEventRail };
});
