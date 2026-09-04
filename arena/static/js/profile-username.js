/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 The NOCA Authors (see AUTHORS)
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/**
 * Username field controller for the Personal & Security tab.
 *
 * The handle posts on its own rather than riding the personal-data form: it has
 * its own cooldown, its own conflict outcome, and its own error vocabulary, and
 * a rename that a deferred Save could silently defer is not the shape anyone
 * expects from an identity field.
 *
 * The availability probe is debounced because it is a per-keystroke database
 * query; the server rate-limits it per user regardless, so this debounce is a
 * courtesy rather than the protection.
 */
(() => {
  "use strict";

  const DEBOUNCE_MS = 300;

  const input = document.getElementById("profile-username-input");
  const saveButton = document.getElementById("profile-username-save");
  const errorSlot = document.getElementById("profile-username-error");
  const helpSlot = document.getElementById("profile-username-help");
  const rulesSlot = document.getElementById("profile-username-rules");
  if (!input || !saveButton) {
    return;
  }

  const checkUrl = input.dataset.checkUrl;
  const updateUrl = input.dataset.updateUrl;
  const jsonHeaders = { "Content-Type": "application/json", Accept: "application/json" };

  let current = input.dataset.currentUsername || "";

  /**
   * Render a message under the field in one of three states.
   *
   * The colours match the admin rename modal deliberately: green for a handle
   * you can have, red for one you cannot. Both pages answer the same question,
   * so answering it in two visual languages would be its own small bug -- which
   * is exactly what happened while this surface wrote every non-error verdict
   * into a muted slot, leaving "available" indistinguishable from help text.
   *
   * @param {"idle"|"ok"|"error"} state How the message should read.
   * @param {string} message Text to show; empty clears the slots.
   */
  const setFeedback = (state, message) => {
    const isError = state === "error";
    input.classList.toggle("is-invalid", isError);
    input.classList.toggle("is-valid", state === "ok");
    // aria-invalid is set alongside the visual class so the field's state is
    // announced, not only coloured. Both slots are aria-live regions the input
    // points at through aria-describedby. Colour is never the only signal: the
    // message itself says which way the verdict went.
    input.setAttribute("aria-invalid", isError ? "true" : "false");
    if (errorSlot) {
      errorSlot.textContent = isError ? message : "";
    }
    // The transient slot is its own element, never the rules slot: the format
    // rules must survive a probe, because the person reading them has one
    // change per cooldown window and needs them most while mid-decision.
    if (helpSlot) {
      helpSlot.textContent = isError ? "" : message || "";
      helpSlot.classList.toggle("text-success", state === "ok" && Boolean(message));
      helpSlot.classList.toggle("text-arena-muted", state !== "ok");
    }
  };

  // The probe itself -- debounce, stale-response guard, and what counts as
  // available -- belongs to the shared module, so this page and the admin rename
  // modal cannot drift apart on it. Only the rendering is local, because this
  // field has an error slot, a transient slot and a rules slot to keep straight.
  const STATE_STYLE = { available: "ok", unavailable: "error", idle: "idle" };
  const availability = window.ArenaUsernameAvailability.bind({
    input,
    checkUrl,
    currentUsername: current,
    debounceMs: DEBOUNCE_MS,
    onResult: (state, message) => setFeedback(STATE_STYLE[state] || "idle", message),
  });

  // This field sits inside the personal-data form, whose submit handler posts
  // everything EXCEPT the username. Left alone, Enter here would report "Changes
  // saved successfully" while silently discarding the handle the user just
  // typed. Enter therefore means this field's own save.
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      saveButton.click();
    }
  });

  saveButton.addEventListener("click", async () => {
    const candidate = input.value.trim();
    if (!candidate) {
      setFeedback("error", "Username is required.");
      input.focus();
      return;
    }
    if (candidate === current) {
      setFeedback("idle", "That is already your username.");
      return;
    }
    saveButton.disabled = true;
    try {
      const response = await fetch(updateUrl, {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify({ username: candidate }),
      });
      const data = await response.json();
      if (!response.ok) {
        setFeedback("error", data.message || "Could not change your username.");
        return;
      }
      current = data.username;
      // The probe holds its own copy of "the handle you already have", so a
      // rename has to move both or the new handle would immediately probe as
      // taken -- by its own owner.
      availability.setCurrentUsername(data.username);
      input.value = data.username;
      input.dataset.currentUsername = data.username;
      setFeedback(
        "ok",
        data.cooldown_days_remaining > 0
          ? `Username changed. You can change it again in ${data.cooldown_days_remaining} day(s).`
          : "Username changed.",
      );
      // The cooldown starts now, so the field must not invite a second attempt
      // the server would only refuse, and the rules it would have to satisfy
      // are no longer the useful thing to say.
      if (data.cooldown_days_remaining > 0) {
        input.disabled = true;
        if (rulesSlot) {
          rulesSlot.textContent = `You can change your username again in ${data.cooldown_days_remaining} day(s).`;
        }
      }
    } catch {
      setFeedback("error", "Could not change your username. Please try again.");
    } finally {
      saveButton.disabled = input.disabled;
    }
  });
})();
