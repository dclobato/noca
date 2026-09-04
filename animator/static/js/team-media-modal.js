//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Reusable team-media modal for the live scoreboard and reveal ceremony. Both
// show the scoped image; only the ceremony opts into the optional audio clip.
//
// Four behaviors here are deliberate, and each exists because the obvious
// implementation is wrong:
//
//   1. The modal is opened by Bootstrap's *data API* (the team button carries
//      data-bs-toggle/data-bs-target), never by modal.show(). In Bootstrap 5.3
//      focus restoration lives in the data-API click handler, so a programmatic
//      show() traps focus correctly but returns it nowhere on close. The team is
//      therefore identified from `event.relatedTarget` in `show.bs.modal`.
//   2. Playback starts on `shown.bs.modal`, inside the user activation the click
//      granted, and every failure is handled. A rejected play() is caught (never
//      an unhandled rejection): NotAllowedError means autoplay was blocked, so
//      the native controls stay and a polite status line invites a press.
//   3. Missing media is distinguished from blocked autoplay by the element's
//      `error` event, not by the rejection alone — verified against Chromium: a
//      404 clip fires error (MEDIA_ERR_SRC_NOT_SUPPORTED) and *then* rejects
//      with NotSupportedError. On that path the player is hidden entirely, so a
//      team with no clip shows a photo-only modal with no broken-player chrome.
//   4. Every media listener is scoped to a load *generation*. Teardown assigns
//      and clears `src`, which can itself deliver an error event; without the
//      generation guard a previous team's teardown would hide the next team's
//      working player (observed in preflight).
//
// Exported as UMD: `window.AnimatorTeamModal` / `module.exports`.
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.AnimatorTeamModal = api;
  }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  var BLOCKED_MESSAGE = "Autoplay was blocked — press play to hear this team.";

  // Common deps: { photoEl, titleEl, photoFallbackEl, photoBase, scope,
  //                fetchImpl, urlApi, AbortControllerCtor, audioEnabled }
  // Audio-enabled mode also requires { audioEl, statusEl, audioBase }, and
  // optionally { blockedMessage } to replace the copy above.
  //
  // The message is injectable because the same modal serves two audiences. On a
  // click it is read by the person who clicked, and "press play" is the right
  // instruction. Opened by an operator's remote cue it is read by a room of
  // spectators who cannot reach the keyboard, so instructing them is worse than
  // useless — the caller supplies copy that states the condition instead, and
  // tells the operator separately.
  //
  // It may therefore be a **function**, resolved at the moment the status is
  // written rather than when the modal was constructed: which audience is
  // reading depends on how *this* open happened, and one dialog instance serves
  // both for the life of the page.
  function createTeamModal(deps) {
    var photoEl = deps.photoEl;
    var audioEnabled = deps.audioEnabled === true;
    var audioEl = audioEnabled ? deps.audioEl : null;
    var titleEl = deps.titleEl;
    var statusEl = audioEnabled ? deps.statusEl : null;
    var photoFallbackEl = deps.photoFallbackEl;
    var blockedMessage = deps.blockedMessage;

    function blockedText() {
      if (typeof blockedMessage === "function") {
        return blockedMessage() || BLOCKED_MESSAGE;
      }
      return typeof blockedMessage === "string" ? blockedMessage : BLOCKED_MESSAGE;
    }
    var generation = 0;
    var pending = null;
    var photoObjectUrl = null;
    var photoAbortController = null;

    function setStatus(text) {
      if (statusEl) {
        statusEl.textContent = text || "";
      }
    }

    function showPhoto(state) {
      if (state === "visible") {
        photoEl.removeAttribute("hidden");
      } else {
        photoEl.setAttribute("hidden", "hidden");
      }
      if (photoFallbackEl) {
        if (state === "failed") {
          photoFallbackEl.removeAttribute("hidden");
        } else {
          photoFallbackEl.setAttribute("hidden", "hidden");
        }
      }
    }

    function mediaUrl(base, teamId, kind) {
      // The scope is the canonical one the server already resolved for this
      // page, so the media request stays inside that presentation's scope.
      return base + "/" + encodeURIComponent(teamId) + "/" + kind + "?scope=" + encodeURIComponent(deps.scope);
    }

    function showPlayer(visible) {
      if (!audioEnabled || !audioEl) {
        return;
      }
      if (visible) {
        audioEl.removeAttribute("hidden");
      } else {
        audioEl.setAttribute("hidden", "hidden");
      }
    }

    function releasePhotoObjectUrl() {
      if (photoObjectUrl) {
        deps.urlApi.revokeObjectURL(photoObjectUrl);
        photoObjectUrl = null;
      }
    }

    // Fetching the existing image response lets the modal read its explicit
    // kind header. Real photos retain their intrinsic size, while the vector
    // placeholder receives the projector-width class. The normal HTTP cache
    // still owns reuse of the scoped media URL.
    function loadPhoto(teamId, mine) {
      showPhoto("loading");
      if (photoAbortController) {
        photoAbortController.abort();
      }
      photoAbortController = deps.AbortControllerCtor ? new deps.AbortControllerCtor() : null;
      var options = photoAbortController ? { signal: photoAbortController.signal } : {};

      deps
        .fetchImpl(mediaUrl(deps.photoBase, teamId, "photo"), options)
        .then(function (response) {
          if (!response.ok) {
            throw new Error("Team photo request failed");
          }
          var kind = response.headers.get("X-NOCA-Team-Image-Kind");
          return response.blob().then(function (blob) {
            return { blob: blob, kind: kind };
          });
        })
        .then(function (result) {
          if (mine !== generation) {
            return;
          }
          releasePhotoObjectUrl();
          photoObjectUrl = deps.urlApi.createObjectURL(result.blob);
          photoEl.classList.toggle("team-media-photo--placeholder", result.kind === "placeholder");
          photoEl.onload = function () {
            if (mine === generation) {
              showPhoto("visible");
            }
          };
          photoEl.onerror = function () {
            if (mine !== generation) {
              return;
            }
            photoEl.removeAttribute("src");
            releasePhotoObjectUrl();
            showPhoto("failed");
          };
          photoEl.setAttribute("src", photoObjectUrl);
        })
        .catch(function (error) {
          if (mine !== generation || (error && error.name === "AbortError")) {
            return;
          }
          showPhoto("failed");
        });
    }

    // Read the team off the element that triggered the modal. Bootstrap passes
    // it as relatedTarget, which is also the element it will refocus on close.
    function onShow(event) {
      var trigger = event && event.relatedTarget;
      if (!trigger || typeof trigger.getAttribute !== "function") {
        pending = null;
        return null;
      }
      var teamId = trigger.getAttribute("data-team-id");
      if (!teamId) {
        pending = null;
        return null;
      }
      pending = {
        teamId: teamId,
        name: trigger.getAttribute("title") || trigger.textContent || "",
      };
      if (titleEl) {
        titleEl.textContent = pending.name;
      }
      setStatus("");
      showPlayer(false);

      var mine = ++generation;
      photoEl.setAttribute("alt", pending.name ? "Photo of " + pending.name : "Team photo");
      loadPhoto(teamId, mine);
      return pending;
    }

    // Assign the clip and attempt playback. Returns a promise for tests; it
    // always resolves (never rejects), reporting which of the three outcomes
    // occurred: "played", "blocked", "unavailable", or "skipped".
    function onShown() {
      if (!audioEnabled || !pending) {
        return Promise.resolve("skipped");
      }
      // One open is one generation: it was assigned by onShow (which also armed
      // the photo handler), and teardown bumps it. Incrementing again here would
      // instantly invalidate this open's own photo listener.
      var mine = generation;
      var settled = false;
      var mediaReady = false;
      var deferredOutcome = null;

      return new Promise(function (resolve) {
        function finish(outcome) {
          if (settled || mine !== generation) {
            return;
          }
          settled = true;
          resolve(outcome);
        }

        function revealDeferredOutcome() {
          if (!deferredOutcome || !mediaReady || mine !== generation) {
            return;
          }
          showPlayer(true);
          setStatus(deferredOutcome === "blocked" ? blockedText() : "");
          finish(deferredOutcome);
        }

        // Registered before src is assigned, so a 404 cannot fire first.
        audioEl.onerror = function () {
          if (mine !== generation) {
            return; // A previous team's teardown; not about this clip.
          }
          showPlayer(false);
          setStatus("");
          finish("unavailable");
        };
        audioEl.onloadedmetadata = function () {
          if (mine !== generation) {
            return;
          }
          mediaReady = true;
          revealDeferredOutcome();
        };

        audioEl.currentTime = 0;
        audioEl.setAttribute("src", mediaUrl(deps.audioBase, pending.teamId, "audio"));
        audioEl.load();

        var result = audioEl.play();
        if (!result || typeof result.then !== "function") {
          deferredOutcome = "played";
          revealDeferredOutcome();
          return;
        }
        result.then(
          function () {
            if (mine === generation) {
              showPlayer(true);
              setStatus("");
              finish("played");
            }
          },
          function (error) {
            if (mine !== generation) {
              return;
            }
            var name = error && error.name;
            if (name === "NotSupportedError" || name === "NotFoundError") {
              // The media itself is unusable; the error handler may already have
              // hidden the player, and finish() is idempotent.
              showPlayer(false);
              setStatus("");
              finish("unavailable");
              return;
            }
            // NotAllowedError and friends: the clip is fine, the browser refused
            // to start it. Wait for metadata before showing controls: a policy
            // rejection can arrive before a missing clip's later error event.
            deferredOutcome = "blocked";
            revealDeferredOutcome();
          },
        );
      });
    }

    // Idempotent teardown: stops playback and any in-flight media download, so
    // closing the modal never leaks one team's work into the next open.
    function teardown() {
      generation += 1; // Invalidate listeners belonging to the media being torn down.
      pending = null;
      if (audioEnabled && audioEl) {
        audioEl.onerror = null;
        audioEl.onloadedmetadata = null;
      }
      photoEl.onload = null;
      photoEl.onerror = null;
      if (photoAbortController) {
        photoAbortController.abort();
        photoAbortController = null;
      }
      photoEl.removeAttribute("src");
      photoEl.classList.remove("team-media-photo--placeholder");
      releasePhotoObjectUrl();
      showPhoto("loading");
      if (audioEnabled && audioEl) {
        try {
          audioEl.pause();
          audioEl.currentTime = 0;
        } catch (ignored) {
          // A media element with no source can refuse currentTime; harmless.
        }
        audioEl.removeAttribute("src");
        audioEl.load();
      }
      showPlayer(false);
      setStatus("");
    }

    return {
      onShow: onShow,
      onShown: onShown,
      teardown: teardown,
      currentTeam: function () {
        return pending;
      },
      generation: function () {
        return generation;
      },
    };
  }

  return {
    BLOCKED_MESSAGE: BLOCKED_MESSAGE,
    createTeamModal: createTeamModal,
  };
});
