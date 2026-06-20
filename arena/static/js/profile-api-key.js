/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/**
 * Profile — Personal API Key modal.
 *
 * Handles the API key set/change modal: submits the key to
 * POST /user/profile/api-key, then updates the inline display
 * in the profile without a page reload.
 *
 * On success the key input is immediately cleared so the plaintext
 * value is never left in the DOM.
 */
(() => {
  "use strict";

  const modal = document.getElementById("profileApiKeyModal");
  if (!modal) {
    return;
  }

  const apiKeyUrl = modal.dataset.apiKeyUrl;
  const keyInput = document.getElementById("profile-api-key-input");
  const saveButton = document.getElementById("profile-api-key-save");
  const errorAlert = document.getElementById("profile-api-key-error");
  const keyDisplay = document.getElementById("profile-api-key-display");
  const keyButton = document.getElementById("profile-api-key-btn");

  // ── Helpers ──────────────────────────────────────────────────────────────────

  const setAlert = (element, message) => {
    if (!element) {
      return;
    }
    element.textContent = message || "";
    element.classList.toggle("d-none", !message);
  };

  /**
   * Return a display-safe masked version of the given key.
   * Shows the first 8 and last 8 characters separated by "...".
   * Falls back to a fully-obscured placeholder for very short keys.
   *
   * @param {string} key - Plaintext key just entered by the user.
   * @returns {string} Masked key string.
   */
  const maskKey = (key) => {
    if (!key || key.length < 16) {
      return "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022...\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022";
    }
    return `${key.slice(0, 8)}...${key.slice(-8)}`;
  };

  const closeModal = () => {
    const bsModal = window.bootstrap?.Modal?.getInstance(modal);
    if (bsModal) {
      bsModal.hide();
    }
  };

  // ── Reset on open ────────────────────────────────────────────────────────────

  modal.addEventListener("show.bs.modal", () => {
    if (keyInput) {
      keyInput.value = "";
    }
    setAlert(errorAlert, "");
  });

  // ── Save handler ─────────────────────────────────────────────────────────────

  if (saveButton) {
    saveButton.addEventListener("click", async () => {
      setAlert(errorAlert, "");
      const rawKey = keyInput ? keyInput.value.trim() : "";

      saveButton.disabled = true;

      try {
        const response = await fetch(apiKeyUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ api_key: rawKey || null }),
        });

        const data = await response.json();

        if (!response.ok) {
          throw new Error(data.error || "Could not save API key.");
        }

        // Update the inline key display
        if (keyDisplay) {
          if (data.cleared) {
            keyDisplay.innerHTML = '<span class="text-arena-muted">No personal API Key</span>';
          } else {
            keyDisplay.innerHTML =
              `<span class="font-monospace text-arena-muted small">${maskKey(rawKey)}</span>`;
          }
        }

        // Update the action button label
        if (keyButton) {
          const spans = keyButton.querySelectorAll("span");
          const labelSpan = spans.length > 0 ? spans[spans.length - 1] : null;
          if (labelSpan) {
            labelSpan.textContent = data.cleared ? "Set a key" : "Change key";
          }
        }

        closeModal();
      } catch (err) {
        setAlert(errorAlert, err.message);
      } finally {
        saveButton.disabled = false;
        // Always wipe the input so the plaintext key is not left in the DOM
        if (keyInput) {
          keyInput.value = "";
        }
      }
    });
  }
})();
