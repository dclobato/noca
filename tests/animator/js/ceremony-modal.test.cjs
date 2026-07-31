//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Browser-independent contract test for ceremony-modal.js. A fake media
// element drives the three playback outcomes that a real browser produces —
// played, blocked (NotAllowedError), and unusable media (error event +
// NotSupportedError) — plus teardown and the stale-generation guard.

"use strict";

const assert = require("assert");
const path = require("path");

const modalApi = require(path.join(__dirname, "..", "..", "..", "animator", "static", "js", "ceremony-modal.js"));

function makeEl() {
  const classes = new Set();
  return {
    attributes: {},
    textContent: "",
    classList: {
      contains(name) {
        return classes.has(name);
      },
      remove(name) {
        classes.delete(name);
      },
      toggle(name, force) {
        if (force) {
          classes.add(name);
        } else {
          classes.delete(name);
        }
      },
    },
    setAttribute(n, v) {
      this.attributes[n] = String(v);
    },
    getAttribute(n) {
      return Object.prototype.hasOwnProperty.call(this.attributes, n) ? this.attributes[n] : null;
    },
    removeAttribute(n) {
      delete this.attributes[n];
    },
  };
}

// A media element that records what was done to it and lets a test decide how
// play() settles.
function makeAudio(behavior) {
  const el = makeEl();
  el.currentTime = 0;
  el.onerror = null;
  el.calls = [];
  el.load = function () {
    el.calls.push("load");
    if (behavior === "blocked") {
      setTimeout(() => {
        if (el.onloadedmetadata) {
          el.onloadedmetadata();
        }
      }, 0);
    }
    if (behavior === "media-error" || behavior === "blocked-media-error") {
      // A real browser fires error asynchronously after a failed fetch.
      setTimeout(() => {
        if (el.onerror) {
          el.onerror();
        }
      }, 0);
    }
  };
  el.pause = function () {
    el.calls.push("pause");
  };
  el.play = function () {
    el.calls.push("play");
    if (behavior === "blocked" || behavior === "blocked-media-error") {
      const e = new Error("blocked");
      e.name = "NotAllowedError";
      return Promise.reject(e);
    }
    if (behavior === "media-error") {
      const e = new Error("unsupported");
      e.name = "NotSupportedError";
      return Promise.reject(e);
    }
    if (behavior === "pending") {
      return new Promise((resolve) => {
        el.resolvePlay = resolve;
      });
    }
    return Promise.resolve();
  };
  return el;
}

function build(behavior, photoKind = "photo", photoBehavior = "ok") {
  const photoEl = makeEl();
  const originalSetAttribute = photoEl.setAttribute;
  photoEl.setAttribute = function (name, value) {
    originalSetAttribute.call(photoEl, name, value);
    if (name === "src" && photoEl.onload) {
      photoEl.onload();
    }
  };
  const audioEl = makeAudio(behavior);
  const titleEl = makeEl();
  const statusEl = makeEl();
  const photoFallbackEl = makeEl();
  const revokedUrls = [];
  const fetchCalls = [];
  photoFallbackEl.setAttribute("hidden", "hidden");
  const modal = modalApi.createTeamModal({
    photoEl,
    audioEl,
    titleEl,
    statusEl,
    photoFallbackEl,
    photoBase: "/c/x/teams",
    audioBase: "/c/x/teams",
    scope: "site-42",
    fetchImpl(url, options) {
      fetchCalls.push({ url, options });
      if (photoBehavior === "network-error") {
        return Promise.reject(new Error("offline"));
      }
      return Promise.resolve({
        ok: true,
        headers: {
          get(name) {
            return name === "X-NOCA-Team-Image-Kind" ? photoKind : null;
          },
        },
        blob() {
          return Promise.resolve({ kind: photoKind });
        },
      });
    },
    urlApi: {
      createObjectURL() {
        return "blob:team-photo-" + (revokedUrls.length + 1);
      },
      revokeObjectURL(url) {
        revokedUrls.push(url);
      },
    },
  });
  return {
    modal,
    photoEl,
    audioEl,
    titleEl,
    statusEl,
    photoFallbackEl,
    fetchCalls,
    revokedUrls,
  };
}

function trigger(teamId, name) {
  const el = makeEl();
  el.setAttribute("data-team-id", teamId);
  el.setAttribute("title", name || "Team " + teamId);
  return { relatedTarget: el };
}

function flushPromises() {
  return new Promise((resolve) => setImmediate(resolve));
}

// ── Scope propagates into both media URLs ────────────────────────────────────
async function testScopePropagation() {
  const { modal, photoEl, audioEl, titleEl, fetchCalls } = build("ok");
  modal.onShow(trigger("team 1/2", "Los Bugs"));
  await flushPromises();

  assert.strictEqual(fetchCalls[0].url, "/c/x/teams/team%201%2F2/photo?scope=site-42");
  assert.ok(photoEl.getAttribute("src").startsWith("blob:team-photo-"));
  assert.strictEqual(titleEl.textContent, "Los Bugs");
  assert.strictEqual(photoEl.getAttribute("alt"), "Photo of Los Bugs");

  await modal.onShown();
  assert.strictEqual(audioEl.getAttribute("src"), "/c/x/teams/team%201%2F2/audio?scope=site-42");
}

// ── The three playback outcomes ──────────────────────────────────────────────
async function testAutoplaySucceeds() {
  const { modal, audioEl, statusEl } = build("ok");
  modal.onShow(trigger("t1"));
  const outcome = await modal.onShown();

  assert.strictEqual(outcome, "played");
  assert.ok(audioEl.calls.includes("play"), "playback is attempted after the modal is shown");
  assert.strictEqual(audioEl.getAttribute("hidden"), null, "the player stays visible");
  assert.strictEqual(statusEl.textContent, "", "a successful play needs no message");
}

async function testControlsStayHiddenUntilAudioIsKnown() {
  const { modal, audioEl } = build("pending");
  modal.onShow(trigger("t1"));
  const outcome = modal.onShown();

  assert.strictEqual(audioEl.getAttribute("hidden"), "hidden", "loading audio must not flash controls");
  audioEl.resolvePlay();
  assert.strictEqual(await outcome, "played");
  assert.strictEqual(audioEl.getAttribute("hidden"), null, "known playable audio reveals controls");
}

async function testBlockedAutoplayKeepsControls() {
  const { modal, audioEl, statusEl } = build("blocked");
  modal.onShow(trigger("t1"));
  const outcome = await modal.onShown();

  assert.strictEqual(outcome, "blocked");
  // The clip is fine — only the browser refused to start it — so the accessible
  // native controls must remain available.
  assert.strictEqual(audioEl.getAttribute("hidden"), null);
  assert.strictEqual(statusEl.textContent, modalApi.BLOCKED_MESSAGE);
}

async function testMissingClipHidesThePlayer() {
  const { modal, audioEl, statusEl, photoEl } = build("media-error");
  modal.onShow(trigger("t1"));
  await flushPromises();
  const outcome = await modal.onShown();

  assert.strictEqual(outcome, "unavailable");
  assert.strictEqual(audioEl.getAttribute("hidden"), "hidden", "no broken player is shown");
  assert.strictEqual(statusEl.textContent, "", "a team without a clip is not an error to report");
  // The photo is untouched: the modal stays usable.
  assert.ok(photoEl.getAttribute("src").startsWith("blob:team-photo-"));
  assert.strictEqual(photoEl.getAttribute("hidden"), null);
}

async function testBlockedAutoplayBeforeMissingClipDoesNotFlash() {
  const { modal, audioEl, statusEl } = build("blocked-media-error");
  modal.onShow(trigger("t1"));
  const outcome = modal.onShown();
  await Promise.resolve();

  assert.strictEqual(audioEl.getAttribute("hidden"), "hidden");
  assert.strictEqual(statusEl.textContent, "");
  assert.strictEqual(await outcome, "unavailable");
  assert.strictEqual(audioEl.getAttribute("hidden"), "hidden");
}

// ── Teardown stops playback and the download, idempotently ───────────────────
async function testTeardownStopsEverything() {
  const { modal, audioEl } = build("ok");
  modal.onShow(trigger("t1"));
  await modal.onShown();
  audioEl.currentTime = 12;

  modal.teardown();
  assert.ok(audioEl.calls.includes("pause"));
  assert.strictEqual(audioEl.currentTime, 0);
  assert.strictEqual(audioEl.getAttribute("src"), null, "removing src also aborts an in-flight download");
  assert.strictEqual(audioEl.calls[audioEl.calls.length - 1], "load");
  assert.strictEqual(audioEl.getAttribute("hidden"), "hidden");

  // `hidden.bs.modal` runs it again as a backstop; it must be harmless.
  const before = audioEl.calls.length;
  modal.teardown();
  assert.ok(audioEl.calls.length > before, "teardown repeats safely");
  assert.strictEqual(audioEl.getAttribute("src"), null);
  assert.strictEqual(modal.currentTeam(), null);
}

async function testSwitchingTeamsStopsThePreviousClip() {
  const { modal, audioEl } = build("ok");
  modal.onShow(trigger("t1"));
  await modal.onShown();
  modal.teardown(); // closing the first team
  modal.onShow(trigger("t2"));
  await modal.onShown();

  assert.strictEqual(audioEl.getAttribute("src"), "/c/x/teams/t2/audio?scope=site-42");
  assert.strictEqual(audioEl.calls.filter((c) => c === "pause").length, 1);
}

// ── The stale-generation guard ───────────────────────────────────────────────
async function testStaleErrorDoesNotHideTheCurrentPlayer() {
  const { modal, audioEl } = build("ok");
  modal.onShow(trigger("t1"));
  await modal.onShown();

  // Capture the previous clip's error handler, then move to another team.
  const staleHandler = audioEl.onerror;
  modal.teardown();
  modal.onShow(trigger("t2"));
  await modal.onShown();
  assert.strictEqual(audioEl.getAttribute("hidden"), null, "the new player is visible");

  // The previous clip's teardown now delivers its error late. Without the
  // generation guard this would hide a perfectly good player.
  if (staleHandler) {
    staleHandler();
  }
  assert.strictEqual(audioEl.getAttribute("hidden"), null, "a stale error must not hide the current clip");
}

// ── A failed photo request never shows broken-image chrome ───────────────────
async function testFailedPhotoFallsBackToACaption() {
  const { modal, photoEl, photoFallbackEl } = build("ok", "photo", "network-error");
  modal.onShow(trigger("t1", "Los Bugs"));
  assert.strictEqual(photoEl.getAttribute("hidden"), "hidden", "the image stays hidden while loading");
  assert.strictEqual(photoFallbackEl.getAttribute("hidden"), "hidden");
  await flushPromises();
  assert.strictEqual(photoFallbackEl.getAttribute("hidden"), null, "a plain caption takes its place");
}

async function testStalePhotoErrorIsIgnored() {
  const { modal, photoEl, photoFallbackEl } = build("ok");
  modal.onShow(trigger("t1"));
  await flushPromises();
  const stalePhotoHandler = photoEl.onerror;
  modal.teardown();
  modal.onShow(trigger("t2"));
  await flushPromises();

  // The previous team's image now reports its failure late.
  stalePhotoHandler();
  assert.strictEqual(photoEl.getAttribute("hidden"), null, "a stale photo error must not hide the current photo");
  assert.strictEqual(photoFallbackEl.getAttribute("hidden"), "hidden");
}

// The photo generation must survive onShown, which runs right after onShow.
async function testPhotoHandlerSurvivesShown() {
  const { modal, photoEl, photoFallbackEl } = build("ok");
  modal.onShow(trigger("t1"));
  await flushPromises();
  await modal.onShown();

  photoEl.onerror();
  assert.strictEqual(photoEl.getAttribute("hidden"), "hidden", "the photo handler is still live after onShown");
  assert.strictEqual(photoFallbackEl.getAttribute("hidden"), null);
}

async function testPlaceholderUsesProjectorWidthAndObjectUrlIsReleased() {
  const { modal, photoEl, revokedUrls } = build("ok", "placeholder");
  modal.onShow(trigger("t1"));
  await flushPromises();

  assert.ok(photoEl.classList.contains("ceremony-team-photo--placeholder"));
  const photoUrl = photoEl.getAttribute("src");
  modal.teardown();
  assert.deepStrictEqual(revokedUrls, [photoUrl]);
  assert.ok(!photoEl.classList.contains("ceremony-team-photo--placeholder"));
}

// ── A trigger without a team is ignored ──────────────────────────────────────
async function testMissingTriggerIsIgnored() {
  const { modal } = build("ok");
  assert.strictEqual(modal.onShow({}), null);
  assert.strictEqual(modal.onShow(trigger("")), null);
  assert.strictEqual(await modal.onShown(), "skipped");
}

(async function main() {
  await testScopePropagation();
  await testAutoplaySucceeds();
  await testControlsStayHiddenUntilAudioIsKnown();
  await testBlockedAutoplayKeepsControls();
  await testMissingClipHidesThePlayer();
  await testBlockedAutoplayBeforeMissingClipDoesNotFlash();
  await testTeardownStopsEverything();
  await testSwitchingTeamsStopsThePreviousClip();
  await testStaleErrorDoesNotHideTheCurrentPlayer();
  await testFailedPhotoFallsBackToACaption();
  await testStalePhotoErrorIsIgnored();
  await testPhotoHandlerSurvivesShown();
  await testPlaceholderUsesProjectorWidthAndObjectUrlIsReleased();
  await testMissingTriggerIsIgnored();
  console.log("ceremony-modal contract: OK");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
