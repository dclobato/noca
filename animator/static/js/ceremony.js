//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Browser glue for the reveal projector. It reads the shell's wiring from data
// attributes, drives RevealTransport (fetch state, then subscribe, refetch on
// every nudge), renders each authoritative projection through CeremonyRender,
// and wires the reusable team modal.
//
// It deliberately owns no ceremony logic: the store is authoritative, the
// transport owns ordering and reconnection, the renderer owns the DOM, and the
// modal owns media. This file only connects them and keeps the header in sync.
(function () {
  "use strict";

  var transportApi = window.RevealTransport;
  var renderApi = window.CeremonyRender;
  var scoreboardRender = window.AnimatorRender;
  var animationApi = window.AnimatorAnimate;
  var modalApi = window.AnimatorTeamModal;
  var root = document.getElementById("ceremony-app");
  if (!transportApi || !renderApi || !scoreboardRender || !animationApi || !modalApi || !root) {
    return;
  }

  var statusEl = document.getElementById("ceremony-status");
  var phaseEl = document.getElementById("ceremony-phase");
  var progressEl = document.getElementById("ceremony-progress");
  var emptyEl = document.getElementById("ceremony-empty");
  var boardScroll = document.querySelector(".ceremony-board-scroll");
  var tbody = document.getElementById("ceremony-standings");
  var headerRow = document.getElementById("ceremony-header-row");
  var modalEl = document.getElementById("team-media-modal");
  var scope = root.getAttribute("data-scope");
  var medalBase = root.getAttribute("data-medal-base");
  var headerProblems = [];
  var rowMotion = animationApi.createApplier(tbody, {
    rowAnimation: animationApi.rowAnimationFromCss(tbody),
  });

  var reducedMotion =
    typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function setStatus(text) {
    if (statusEl) {
      statusEl.textContent = text;
    }
  }

  // ── Team modal ────────────────────────────────────────────────────────────
  var teamModal = modalApi.createTeamModal({
    audioEnabled: true,
    photoEl: document.getElementById("team-media-photo"),
    audioEl: document.getElementById("team-media-audio"),
    titleEl: document.getElementById("team-media-modal-label"),
    statusEl: document.getElementById("team-media-audio-status"),
    photoFallbackEl: document.getElementById("team-media-photo-fallback"),
    photoBase: root.getAttribute("data-photo-base"),
    audioBase: root.getAttribute("data-audio-base"),
    scope: scope,
    fetchImpl: window.fetch.bind(window),
    urlApi: window.URL,
    AbortControllerCtor: window.AbortController,
  });

  if (modalEl) {
    // Bootstrap's data API opens the dialog (the team button is the trigger), so
    // these handlers only fill it in and manage media. Teardown runs on `hide`
    // and again on `hidden` — it is idempotent, and the second call is the
    // backstop for a dismissal path that skips the first.
    modalEl.addEventListener("show.bs.modal", function (event) {
      teamModal.onShow(event);
    });
    modalEl.addEventListener("shown.bs.modal", function () {
      teamModal.onShown();
    });
    modalEl.addEventListener("hide.bs.modal", function () {
      teamModal.teardown();
    });
    modalEl.addEventListener("hidden.bs.modal", function () {
      teamModal.teardown();
    });
  }

  // ── Rendering ─────────────────────────────────────────────────────────────
  function scrollFocusedIntoView(focusedTeamId) {
    if (!focusedTeamId || !tbody) {
      return;
    }
    var row = tbody.querySelector('tr[data-team-id="' + window.CSS.escape(focusedTeamId) + '"]');
    if (row && typeof row.scrollIntoView === "function") {
      row.scrollIntoView({ block: "center", behavior: reducedMotion ? "auto" : "smooth" });
    }
  }

  function render(state) {
    var projection = state && state.projection;
    var hasTeams = !!(projection && projection.teams && projection.teams.length);

    if (phaseEl) {
      var phase = projection ? projection.phase : "none";
      phaseEl.setAttribute("data-phase", phase);
      phaseEl.textContent = phase === "none" ? "Idle" : phase.charAt(0).toUpperCase() + phase.slice(1);
    }
    if (progressEl) {
      progressEl.textContent = renderApi.renderProgress(projection);
    }
    root.setAttribute("data-phase", projection ? projection.phase : "none");

    if (!hasTeams) {
      if (tbody) {
        while (tbody.firstChild) {
          tbody.removeChild(tbody.firstChild);
        }
      }
      if (emptyEl) {
        emptyEl.hidden = false;
      }
      if (boardScroll) {
        boardScroll.hidden = true;
      }
      return;
    }

    // Measure before replacing the rows, then invert every surviving team to
    // its former screen position. Releasing that transform makes a team glide
    // to its new rank instead of jumping there when a solve changes standings.
    var firstTops = rowMotion.measureRows();
    renderApi.renderStandings(document, tbody, headerRow, projection, {
      medalBase: medalBase,
      headerProblems: headerProblems,
    });
    rowMotion.apply(null, firstTops);
    if (emptyEl) {
      emptyEl.hidden = true;
    }
    if (boardScroll) {
      boardScroll.hidden = false;
    }
    scrollFocusedIntoView(projection.focused_team_id);
  }

  // ── Transport ─────────────────────────────────────────────────────────────
  function fetchState() {
    return window
      .fetch(root.getAttribute("data-state-url"), { headers: { Accept: "application/json" } })
      .then(function (response) {
        if (!response.ok) {
          var error = new Error("reveal state request failed with status " + response.status);
          error.status = response.status;
          // The store's 503 carries Retry-After; pass it to the transport so a
          // retry waits at least as long as the server asked for.
          var hint = parseInt(response.headers.get("Retry-After"), 10);
          if (!isNaN(hint)) {
            error.retryAfterSeconds = hint;
          }
          throw error;
        }
        return response.json();
      });
  }

  // Use the live scoreboard's public problem metadata and pure header renderer,
  // so both presentations show the same configured order and labeled balloons.
  // A metadata failure is non-fatal: the reveal projection can still render its
  // plain-text labels and the ceremony transport must remain available.
  function fetchHeaderProblems() {
    return window
      .fetch(root.getAttribute("data-meta-url"), { headers: { Accept: "application/json" } })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("contest metadata request failed with status " + response.status);
        }
        return response.json();
      })
      .then(function (meta) {
        headerProblems = scoreboardRender.extractProblems(meta, {
          balloonBase: root.getAttribute("data-balloon-base"),
          starBase: root.getAttribute("data-star-base"),
        });
      })
      .catch(function () {
        headerProblems = [];
      });
  }

  var transport = transportApi.createRevealTransport({
    fetchState: fetchState,
    EventSourceCtor: window.EventSource,
    eventsUrl: root.getAttribute("data-events-url"),
    onState: function (state) {
      window.revealState = state;
      render(state);
      setStatus(state && state.has_session ? "" : "Waiting for the ceremony to start…");
      root.dispatchEvent(new CustomEvent("reveal:state", { detail: state }));
    },
    onError: function (_error, retryable) {
      // The last good projection stays on screen: a projector must never blank
      // out mid-ceremony because one refetch failed.
      setStatus(
        retryable ? "The ceremony state could not be loaded; retrying…" : "The ceremony state could not be loaded.",
      );
    },
    onConnectionError: function () {
      // The last projection remains visible while the transport replaces the
      // failed EventSource. `reveal_ready` clears this after subscription
      // coverage is restored and the authoritative state has been fetched.
      setStatus("Connection lost; reconnecting…");
    },
  });

  window.revealTransport = transport;
  transport.start();
  fetchHeaderProblems().then(function () {
    if (window.revealState) {
      render(window.revealState);
    }
  });
})();
