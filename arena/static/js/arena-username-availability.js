/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 The NOCA Authors (see AUTHORS)
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/**
 * Live "is this handle free?" probe, shared by every surface that renames one.
 *
 * Two surfaces need it and they are not the same shape: a user renaming
 * themselves has a field with its own Save button, while an administrator
 * renaming someone else has a field inside a native POST form. Only the
 * *probing* is common — the debounce, the stale-response guard, and the
 * decision of what counts as available — so that is what lives here, and each
 * surface keeps its own rendering.
 *
 * Two ways to use it:
 *
 * - **Declaratively.** Any input carrying `data-username-check-url` is bound on
 *   DOM ready, and its verdict is written into the element named by
 *   `data-username-status`. `data-username-current` names the handle already
 *   held, which never counts as taken.
 * - **Programmatically**, through `window.ArenaUsernameAvailability.bind()`,
 *   when the caller wants to render the verdict itself.
 *
 * The stale-response guard matters more than it looks. Typing "ana" then "anna"
 * fires two probes; without the token the slower first answer can land last and
 * label "anna" with "ana"'s verdict. Every response is therefore discarded
 * unless it belongs to the most recent probe.
 */
(() => {
  "use strict";

  const DEFAULT_DEBOUNCE_MS = 300;

  /**
   * Bind a debounced availability probe to one input.
   *
   * @param {object} options Binding options.
   * @param {HTMLInputElement} options.input The field being typed into.
   * @param {string} options.checkUrl Endpoint answering `{username, available, error}`.
   * @param {string} [options.currentUsername] Handle already held; never "taken".
   * @param {number} [options.debounceMs] Idle time before probing.
   * @param {Function} options.onResult Called as `(state, message, data)` where
   *   state is "idle", "available" or "unavailable".
   * @returns {object} `{ probe, setCurrentUsername }` for the caller to drive.
   */
  const bind = ({ input, checkUrl, currentUsername = "", debounceMs = DEFAULT_DEBOUNCE_MS, onResult }) => {
    let current = currentUsername;
    let debounceTimer = null;
    let token = 0;

    const probe = async () => {
      const candidate = input.value.trim();
      // An empty field is not a verdict, and neither is the handle the account
      // already holds: saying "unavailable" about someone's own name would be
      // both wrong and alarming.
      if (!candidate || candidate.toLowerCase() === String(current).toLowerCase()) {
        onResult("idle", "", null);
        return;
      }
      const mine = ++token;
      try {
        const response = await fetch(`${checkUrl}?q=${encodeURIComponent(candidate)}`, {
          headers: { Accept: "application/json" },
        });
        if (mine !== token) {
          return;
        }
        if (response.status === 429) {
          onResult("unavailable", "Too many username checks. Please wait a moment.", null);
          return;
        }
        const data = await response.json();
        if (mine !== token) {
          return;
        }
        if (data.available) {
          onResult("available", `${data.username} is available.`, data);
        } else {
          onResult("unavailable", data.error || "That username is not available.", data);
        }
      } catch {
        // A failed probe is not a verdict. Reporting "unavailable" for a dropped
        // connection would talk someone out of a handle they can have.
        if (mine === token) {
          onResult("idle", "", null);
        }
      }
    };

    input.addEventListener("input", () => {
      window.clearTimeout(debounceTimer);
      debounceTimer = window.setTimeout(probe, debounceMs);
    });

    return {
      probe,
      setCurrentUsername: (value) => {
        current = value;
      },
    };
  };

  /**
   * Bind every declaratively marked input on the page.
   *
   * Idempotent: an input already bound is skipped, so this is safe to call again
   * after markup is injected.
   */
  const bindAll = () => {
    document.querySelectorAll("[data-username-check-url]").forEach((input) => {
      if (input.dataset.usernameBound === "1") {
        return;
      }
      input.dataset.usernameBound = "1";
      const status = input.dataset.usernameStatus ? document.getElementById(input.dataset.usernameStatus) : null;
      bind({
        input,
        checkUrl: input.dataset.usernameCheckUrl,
        currentUsername: input.dataset.usernameCurrent || "",
        onResult: (state, message) => {
          input.classList.toggle("is-invalid", state === "unavailable");
          input.classList.toggle("is-valid", state === "available");
          input.setAttribute("aria-invalid", state === "unavailable" ? "true" : "false");
          if (status) {
            status.textContent = message;
            status.classList.toggle("text-danger", state === "unavailable");
            status.classList.toggle("text-success", state === "available");
          }
        },
      });
    });
  };

  window.ArenaUsernameAvailability = { bind, bindAll };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindAll);
  } else {
    bindAll();
  }
})();
