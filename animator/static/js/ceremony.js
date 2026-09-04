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
//
// The operator's team-media cue follows the same split: `ceremony-media-cue.js`
// owns what a cue *means* for the projector -- including the rule that a
// ceremony which moves takes the overlay down -- and this file only supplies it
// with the DOM.
(function () {
  "use strict";

  var transportApi = window.RevealTransport;
  var renderApi = window.CeremonyRender;
  var scoreboardRender = window.AnimatorRender;
  var animationApi = window.AnimatorAnimate;
  var modalApi = window.AnimatorTeamModal;
  var cueApi = window.CeremonyMediaCue;
  var root = document.getElementById("ceremony-app");
  if (!transportApi || !renderApi || !scoreboardRender || !animationApi || !modalApi || !cueApi || !root) {
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
  var audioHintEl = document.getElementById("ceremony-audio-hint");
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
  // Two audiences, two messages. A click is read by the person who clicked, so
  // the module default ("press play") is right. A remote cue is read by the
  // room, which cannot reach this keyboard, so the overlay states the condition
  // and the operator is told what to do about it in the header instead.
  var REMOTE_BLOCKED_MESSAGE = "Audio is muted on this display.";

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
    blockedMessage: function () {
      return mediaCue.openedRemotely() ? REMOTE_BLOCKED_MESSAGE : null;
    },
  });

  function setAudioHint(visible) {
    if (audioHintEl) {
      audioHintEl.hidden = !visible;
    }
  }

  var mediaCue = cueApi.createMediaCueController({
    findTrigger: function (teamId) {
      if (!tbody) {
        return null;
      }
      return tbody.querySelector('[data-team-media-trigger][data-team-id="' + window.CSS.escape(teamId) + '"]');
    },
    isOpen: function () {
      return !!(modalEl && modalEl.classList.contains("show"));
    },
    close: function () {
      if (!modalEl || !window.bootstrap) {
        return;
      }
      var instance = window.bootstrap.Modal.getInstance(modalEl);
      if (instance) {
        instance.hide();
      }
    },
    currentTeamId: function () {
      var current = teamModal.currentTeam();
      return current ? current.teamId : null;
    },
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
      var remote = mediaCue.openedRemotely();
      teamModal.onShown().then(function (outcome) {
        // Only a *remotely* opened clip earns the hint: a local click was
        // performed by the one person who can grant this page audio, and the
        // overlay's own "press play" already reached them.
        setAudioHint(remote && outcome === "blocked");
      });
    });
    modalEl.addEventListener("hide.bs.modal", function () {
      teamModal.teardown();
    });
    modalEl.addEventListener("hidden.bs.modal", function () {
      teamModal.teardown();
      setAudioHint(false);
      mediaCue.onHidden();
    });
  }

  // A click anywhere gives this document user activation, so the next remotely
  // cued clip will play. Retire the hint that asked for exactly that.
  document.addEventListener(
    "pointerdown",
    function () {
      setAudioHint(false);
    },
    true,
  );

  // ── Rendering ─────────────────────────────────────────────────────────────
  // Scrolls only the board container. `scrollIntoView` would also scroll every
  // ancestor -- and a browser scrolls an `overflow: hidden` body or viewport
  // programmatically even though the user cannot scroll it back -- so the
  // footer could end up parked in the middle of the projector with no way out.
  function scrollFocusedIntoView(focusedTeamId) {
    if (!focusedTeamId || !tbody || !boardScroll) {
      return;
    }
    var row = tbody.querySelector('tr[data-team-id="' + window.CSS.escape(focusedTeamId) + '"]');
    if (!row) {
      return;
    }
    var rowRect = row.getBoundingClientRect();
    var boardRect = boardScroll.getBoundingClientRect();
    var rowTop = rowRect.top - boardRect.top + boardScroll.scrollTop;
    var target = Math.max(0, rowTop - (boardScroll.clientHeight - rowRect.height) / 2);
    if (typeof boardScroll.scrollTo === "function") {
      boardScroll.scrollTo({ top: target, behavior: reducedMotion ? "auto" : "smooth" });
    } else {
      boardScroll.scrollTop = target;
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
      .fetch(root.getAttribute("data-meta-url"), { headers: { Accept: "application/json" }, cache: "no-store" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("contest metadata request failed with status " + response.status);
        }
        return response.json();
      })
      .then(function (meta) {
        headerProblems = scoreboardRender.extractProblems(meta, {
          balloonBase: root.getAttribute("data-balloon-base"),
        });
        scoreboardRender.renderProblemColors(document, headerProblems);
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
      mediaCue.applyState(state && state.projection);
      render(state);
      setStatus(state && state.has_session ? "" : "Waiting for the ceremony to start…");
      root.dispatchEvent(new CustomEvent("reveal:state", { detail: state }));
    },
    onMediaCue: mediaCue.apply,
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
