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
  var modalApi = window.AnimatorTeamModal;

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

  // The transport always applies through the external path: every snapshot it
  // produces arrives without a matching /meta.
  function startLive(refs, applyExternal, onSubmission, connectionStatus) {
    var coordinator = live.createRefreshCoordinator(
      function () {
        return fetchJson(refs.snapshotUrl);
      },
      applyExternal,
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

  // Pre-start re-check. The live transport carries the contest *start* no better
  // than it carries anything else: no submission exists yet, so no event fires
  // at the start instant and a refreshed /snapshot alone would still be missing
  // the problem set. Only /meta publishes that, and only from the start onward —
  // so a projector opened early re-reads meta on a timer until the gate opens.
  //
  // This deliberately does NOT touch the live connection: the badge keeps
  // reporting Live/Reconnecting/Polling from the moment the page loads, which is
  // what tells the room the board will not miss a second once the clock starts.
  // The delay is capped at a minute so an hours-away start neither overflows
  // setTimeout's 32-bit range nor trusts a sleeping tab's clock.
  var MAX_START_RECHECK_MS = 60000;
  var startRecheckHandle = null;
  // True while the last applied snapshot reported a not-yet-started contest.
  // Module-level alongside the timer handle above: the page owns exactly one
  // board, and the two pieces of state are one machine.
  var awaitingStart = false;

  function cancelStartRecheck() {
    if (startRecheckHandle !== null) {
      window.clearTimeout(startRecheckHandle);
      startRecheckHandle = null;
    }
  }

  function scheduleStartRecheck(meta, rerun) {
    cancelStartRecheck();
    var startMs = Date.parse(meta && meta.start_time);
    var remaining = isNaN(startMs) ? MAX_START_RECHECK_MS : startMs - Date.now() + 1000;
    var delay = Math.max(1000, Math.min(remaining, MAX_START_RECHECK_MS));
    startRecheckHandle = window.setTimeout(rerun, delay);
  }

  // Apply one /meta to the page chrome and the board's problem columns. Shared
  // by the initial load and the pre-start re-check, so the columns that appear
  // at the start instant are built exactly the way the initial ones are.
  function applyMeta(refs, timer, board, meta, frozen) {
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
    timer.configure(meta, frozen);
  }

  // Re-read both feeds after the start instant, then apply them *directly*.
  //
  // `appliers.direct` is used rather than `appliers.external` because this
  // function has just fetched the pair itself: routing its own result back
  // through the external guard would trip that guard and send this same function
  // off to fetch a second time, discarding the snapshot already in hand — a
  // wasted round trip at the most-watched instant of the contest. The guard is
  // for snapshots that arrive from the live transport, which carry no meta.
  function recheckStart(refs, timer, board, appliers) {
    Promise.all([fetchJson(refs.metaUrl), fetchJson(refs.snapshotUrl)])
      .then(function (results) {
        var meta = results[0];
        var snapshot = results[1];
        if (snapshot.has_started === false) {
          awaitingStart = true;
          scheduleStartRecheck(meta, function () {
            recheckStart(refs, timer, board, appliers);
          });
          return;
        }
        awaitingStart = false;
        board.reset();
        applyMeta(refs, timer, board, meta, snapshot.is_frozen);
        appliers.direct(snapshot);
      })
      .catch(function () {
        // A failed re-check is not a page failure — the board is showing the
        // correct pre-start banner and the live badge owns connection health.
        // Try again on the next tick rather than tearing the page down.
        scheduleStartRecheck(null, function () {
          recheckStart(refs, timer, board, appliers);
        });
      });
  }

  function load(refs, timer, board, appliers, onSubmission, connectionStatus) {
    cancelStartRecheck();
    setHidden(refs.loading, false);
    setHidden(refs.error, true);
    setHidden(refs.empty, true);
    setHidden(refs.notStarted, true);
    setHidden(refs.board, true);
    Promise.all([fetchJson(refs.metaUrl), fetchJson(refs.snapshotUrl)])
      .then(function (results) {
        var meta = results[0];
        var snapshot = results[1];
        awaitingStart = snapshot.has_started === false;
        applyMeta(refs, timer, board, meta, snapshot.is_frozen);
        // Direct, for the same reason as in recheckStart: this pair was fetched
        // here, so the external guard has nothing to add and would only bounce.
        appliers.direct(snapshot);
        // The live transport is started for a not-yet-started contest exactly as
        // for a running one, so the connection badge is already reporting Live
        // while the audience watches the countdown. What it cannot do is deliver
        // the start itself, hence the re-check below.
        var finalReleased = render.isReleasedFinal(meta, snapshot, Date.now());
        setHidden(refs.connection, finalReleased);
        if (!finalReleased) {
          startLive(refs, appliers.external, onSubmission, connectionStatus);
        }
        if (awaitingStart) {
          scheduleStartRecheck(meta, function () {
            recheckStart(refs, timer, board, appliers);
          });
        }
      })
      .catch(function () {
        setHidden(refs.loading, true);
        setHidden(refs.board, true);
        setHidden(refs.empty, true);
        setHidden(refs.notStarted, true);
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
      !connectionStatusFactory || !eventFactory || !modalApi
    ) {
      return;
    }
    var refs = {
      metaUrl: app.getAttribute("data-meta-url"),
      snapshotUrl: app.getAttribute("data-snapshot-url"),
      eventsUrl: app.getAttribute("data-events-url"),
      scope: app.getAttribute("data-scope"),
      scopeName: app.getAttribute("data-scope-name"),
      photoBase: app.getAttribute("data-photo-base"),
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
      notStarted: document.getElementById("animator-not-started"),
      board: app.querySelector(".animator-board-scroll"),
      problemHeader: document.getElementById("animator-problem-header"),
      standings: document.getElementById("animator-standings"),
      pending: document.getElementById("animator-pending"),
      pendingList: document.getElementById("animator-pending-list"),
      teamModal: document.getElementById("team-media-modal"),
      teamModalTitle: document.getElementById("team-media-modal-label"),
      teamPhoto: document.getElementById("team-media-photo"),
      teamPhotoFallback: document.getElementById("team-media-photo-fallback"),
    };
    var teamModal = modalApi.createTeamModal({
      audioEnabled: false,
      photoEl: refs.teamPhoto,
      titleEl: refs.teamModalTitle,
      photoFallbackEl: refs.teamPhotoFallback,
      photoBase: refs.photoBase,
      scope: refs.scope,
      fetchImpl: window.fetch.bind(window),
      urlApi: window.URL,
      AbortControllerCtor: window.AbortController,
    });
    if (refs.teamModal) {
      refs.teamModal.addEventListener("show.bs.modal", function (event) {
        teamModal.onShow(event);
      });
      refs.teamModal.addEventListener("hide.bs.modal", function () {
        teamModal.teardown();
      });
      refs.teamModal.addEventListener("hidden.bs.modal", function () {
        teamModal.teardown();
      });
    }
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

    // Apply a snapshot whose matching /meta is already on the page. Both fetch
    // sites use this: they read meta and snapshot together, so there is nothing
    // for the start gate to decide.
    function applyDirect(snapshot, refreshSequence) {
      board.applySnapshot(snapshot);
      pendingFlashes.apply(snapshot, refreshSequence);
    }

    // Apply a snapshot that arrived from the live transport (SSE or the polling
    // fallback). Only this path can be surprised by the start: the transport
    // delivers snapshots but never meta, and standings cannot be drawn without
    // the problem columns that only /meta carries. So the single case this guard
    // exists for is "the live transport delivered the start before the timer
    // did" -- a submission in the opening seconds is enough -- and it answers by
    // fetching the pair rather than rendering rows with no problem cells.
    function applyExternal(snapshot, refreshSequence) {
      if (awaitingStart && snapshot.has_started !== false) {
        awaitingStart = false;
        cancelStartRecheck();
        recheckStart(refs, timer, board, appliers);
        return;
      }
      awaitingStart = snapshot.has_started === false;
      applyDirect(snapshot, refreshSequence);
    }

    var appliers = { direct: applyDirect, external: applyExternal };

    function onSubmission(data, refreshSequence) {
      pendingFlashes.enqueue(data, refreshSequence);
      refs.activity.observeSubmission(data);
    }

    if (refs.retry) {
      refs.retry.addEventListener("click", function () {
        board.reset();
        load(refs, timer, board, appliers, onSubmission, connectionStatus);
      });
    }
    load(refs, timer, board, appliers, onSubmission, connectionStatus);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
