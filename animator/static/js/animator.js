//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Animator live-scoreboard orchestrator. Thin browser glue: it loads /meta and
// /snapshot once, drives a locally ticking contest timer, and hands live wiring
// to AnimatorLive (one EventSource, coalesced/stale-safe refreshes, the
// Live/Reconnecting/Polling machine) with AnimatorRender drawing rows in
// authoritative server order and AnimatorAnimate applying FLIP + transient
// highlights. The pure/testable pieces live in animator-diff/animate/live.js.
(function () {
  "use strict";

  var render = window.AnimatorRender;
  var diff = window.AnimatorDiff;
  var animate = window.AnimatorAnimate;
  var live = window.AnimatorLive;
  var boardFactory = window.AnimatorBoard;
  var pendingFactory = window.AnimatorPending;
  var connectionStatusFactory = window.AnimatorConnectionStatus;
  var eventFactory = window.AnimatorEvents;

  function setHidden(el, hidden) {
    if (!el) {
      return;
    }
    if (hidden) {
      el.setAttribute("hidden", "");
    } else {
      el.removeAttribute("hidden");
    }
  }

  // Local contest timer: countdown before start, elapsed during, pinned final
  // duration after end, frozen indicator from the snapshot. The interval is
  // scheduled only while the projection reports `running`, so an ended or
  // invalid-timing contest never leaves a permanent 1s interval behind.
  function Timer(stateEl, endedEl, timerEl) {
    this.stateEl = stateEl;
    this.endedEl = endedEl;
    this.timerEl = timerEl;
    this.startMs = null;
    this.endMs = null;
    this.frozen = false;
    this.handle = null;
  }

  Timer.prototype._render = function () {
    var view = render.computeTimerView(this.startMs, this.endMs, this.frozen, Date.now());
    if (this.stateEl) {
      this.stateEl.setAttribute("data-state", view.state);
      this.stateEl.textContent = view.label;
    }
    setHidden(this.endedEl, !view.ended);
    if (this.timerEl) {
      this.timerEl.textContent = view.text;
    }
    return view;
  };

  Timer.prototype.stop = function () {
    if (this.handle !== null) {
      window.clearInterval(this.handle);
      this.handle = null;
    }
  };

  Timer.prototype.tick = function () {
    var view = this._render();
    if (!view.running) {
      this.stop();
    }
  };

  Timer.prototype._applyRunningState = function () {
    var view = this._render();
    if (view.running) {
      if (this.handle === null) {
        this.handle = window.setInterval(this.tick.bind(this), 1000);
      }
    } else {
      this.stop();
    }
  };

  Timer.prototype.configure = function (meta, frozen) {
    var start = Date.parse(meta.start_time);
    var end = Date.parse(meta.end_time);
    this.startMs = isNaN(start) ? null : start;
    this.endMs = isNaN(end) ? null : end;
    this.frozen = Boolean(frozen);
    this._applyRunningState();
  };

  // Sync freeze state from a live snapshot: if the contest freezes (or unfreezes)
  // while the page is open, flip the header label without a reload.
  Timer.prototype.setFrozen = function (frozen) {
    var next = Boolean(frozen);
    if (next === this.frozen) {
      return;
    }
    this.frozen = next;
    this._applyRunningState();
  };

  function fetchJson(url) {
    return fetch(url, { headers: { Accept: "application/json" } }).then(function (response) {
      if (!response.ok) {
        throw new Error("HTTP " + response.status + " for " + url);
      }
      return response.json();
    });
  }

  function startLive(refs, applySnapshot, onSubmission, connectionStatus) {
    var coordinator = live.createRefreshCoordinator(
      function () {
        return fetchJson(refs.snapshotUrl);
      },
      applySnapshot,
    );
    var pollHandle = null;
    var controller = live.createConnectionController({
      EventSourceCtor: window.EventSource || null,
      eventsUrl: refs.eventsUrl,
      coordinator: coordinator,
      startPolling: function (pollOnce) {
        if (pollHandle === null) {
          pollHandle = window.setInterval(pollOnce, refs.pollMs);
        }
      },
      stopPolling: function () {
        if (pollHandle !== null) {
          window.clearInterval(pollHandle);
          pollHandle = null;
        }
      },
      setStatus: function (status) {
        connectionStatus.setStatus(status);
      },
      onSubmission: onSubmission,
      onVerdict: refs.activity.observeVerdict,
    });
    controller.connect();
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) {
        controller.refreshNow();
      }
    });
    // Only tear down on a real unload. A persisted `pagehide` means the page is
    // entering the back/forward cache; closing then would leave it dead on
    // restore, since init never reruns. Reconnect instead on a persisted
    // `pageshow`.
    window.addEventListener("pagehide", function (event) {
      if (!event.persisted) {
        controller.close();
      }
    });
    window.addEventListener("pageshow", function (event) {
      if (event.persisted) {
        controller.reopen();
      }
    });
  }

  function load(refs, timer, board, applySnapshot, onSubmission, connectionStatus) {
    setHidden(refs.loading, false);
    setHidden(refs.error, true);
    setHidden(refs.empty, true);
    setHidden(refs.board, true);
    Promise.all([fetchJson(refs.metaUrl), fetchJson(refs.snapshotUrl)])
      .then(function (results) {
        var meta = results[0];
        var snapshot = results[1];
        var problems = render.extractProblems(meta, {
          balloonBase: refs.balloonBase,
          starBase: refs.starBase,
        });
        if (refs.title) {
          refs.title.textContent = meta.name || "Scoreboard";
        }
        if (refs.site) {
          if (refs.scopeName) {
            refs.site.textContent = refs.scopeName;
            refs.site.hidden = false;
          } else {
            refs.site.hidden = true;
          }
        }
        render.renderHeader(document, refs.problemHeader, problems);
        board.setProblems(problems);
        refs.activity.configure(meta);
        timer.configure(meta, snapshot.is_frozen);
        applySnapshot(snapshot);
        var finalReleased = render.isReleasedFinal(meta, snapshot, Date.now());
        setHidden(refs.connection, finalReleased);
        if (!finalReleased) {
          startLive(refs, applySnapshot, onSubmission, connectionStatus);
        }
      })
      .catch(function () {
        setHidden(refs.loading, true);
        setHidden(refs.board, true);
        setHidden(refs.empty, true);
        setHidden(refs.error, false);
      });
  }

  function parsePollMs(app) {
    var raw = parseInt(app.getAttribute("data-poll-fallback"), 10);
    var seconds = isNaN(raw) || raw < 1 ? 15 : raw;
    return seconds * 1000;
  }

  function init() {
    var app = document.getElementById("animator-app");
    if (
      !app || !render || !diff || !animate ||
      !live || !boardFactory || !pendingFactory ||
      !connectionStatusFactory || !eventFactory
    ) {
      return;
    }
    var refs = {
      metaUrl: app.getAttribute("data-meta-url"),
      snapshotUrl: app.getAttribute("data-snapshot-url"),
      eventsUrl: app.getAttribute("data-events-url"),
      scopeName: app.getAttribute("data-scope-name"),
      balloonBase: app.getAttribute("data-balloon-base"),
      starBase: app.getAttribute("data-star-base"),
      medalBase: app.getAttribute("data-medal-base"),
      pollMs: parsePollMs(app),
      title: document.getElementById("animator-contest-title"),
      site: document.getElementById("animator-contest-site"),
      state: document.getElementById("animator-contest-state"),
      ended: document.getElementById("animator-contest-ended"),
      timer: document.getElementById("animator-timer"),
      connection: document.getElementById("animator-connection"),
      connectionLabel: document.getElementById("animator-connection-label"),
      connectionElapsed: document.getElementById("animator-connection-elapsed"),
      loading: document.getElementById("animator-loading"),
      error: document.getElementById("animator-error"),
      retry: document.getElementById("animator-retry"),
      empty: document.getElementById("animator-empty"),
      board: app.querySelector(".animator-board-scroll"),
      problemHeader: document.getElementById("animator-problem-header"),
      standings: document.getElementById("animator-standings"),
      pending: document.getElementById("animator-pending"),
      pendingList: document.getElementById("animator-pending-list"),
    };
    refs.activity = eventFactory.createEventRail({
      doc: document,
      container: document.getElementById("animator-events"),
      list: document.getElementById("animator-events-list"),
      now: Date.now,
      reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)"),
    });
    var timer = new Timer(refs.state, refs.ended, refs.timer);
    var connectionStatus = connectionStatusFactory.createConnectionStatus({
      container: refs.connection,
      label: refs.connectionLabel,
      timer: refs.connectionElapsed,
      now: Date.now,
      setInterval: window.setInterval.bind(window),
      clearInterval: window.clearInterval.bind(window),
    });
    var applier = animate.createApplier(refs.standings, {});
    var pending = pendingFactory.createPendingList({
      doc: document,
      container: refs.pending,
      list: refs.pendingList,
    });
    var board = boardFactory.createBoard({
      doc: document,
      render: render,
      diff: diff,
      applier: applier,
      timer: timer,
      pending: pending,
      activity: refs.activity,
      refs: refs,
      setHidden: setHidden,
    });

    var pendingFlashes = live.createPendingFlashQueue({
      cellKey: diff.cellKey,
      flashCell: function (teamId, problemId) {
        applier.flashCell(teamId, problemId, "animator-cell--flash-pending");
      },
    });

    function applySnapshot(snapshot, refreshSequence) {
      board.applySnapshot(snapshot);
      pendingFlashes.apply(snapshot, refreshSequence);
    }

    function onSubmission(data, refreshSequence) {
      pendingFlashes.enqueue(data, refreshSequence);
      refs.activity.observeSubmission(data);
    }

    if (refs.retry) {
      refs.retry.addEventListener("click", function () {
        board.reset();
        load(refs, timer, board, applySnapshot, onSubmission, connectionStatus);
      });
    }
    load(refs, timer, board, applySnapshot, onSubmission, connectionStatus);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
