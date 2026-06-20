/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

(() => {
  "use strict";

  const pane = document.getElementById("notifications-tab-pane");
  if (!pane) {
    return;
  }

  /**
   * Intercept delete form submissions and ask for confirmation before
   * allowing the POST to proceed.
   */
  pane.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-confirm]");
    if (!form) {
      return;
    }
    const message = form.dataset.confirm || "Remove this notification?";
    if (!window.confirm(message)) {
      event.preventDefault();
    }
  });

  /**
   * When a notification link is clicked:
   * 1. Prevent the default navigation.
   * 2. Call the mark-as-read endpoint (fire-and-forget, errors are ignored).
   * 3. Navigate to the target URL.
   *
   * If the link has no real target URL (href="#") we skip the navigation step.
   */
  pane.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-read-url]");
    if (!link) {
      return;
    }

    const readUrl = link.dataset.readUrl;
    const targetHref = link.href;

    // Treat "#" (no real target) as navigation-less — just mark as read.
    const hasTarget = targetHref && !targetHref.endsWith("#");

    if (hasTarget) {
      event.preventDefault();
    }

    if (readUrl) {
      link.classList.remove("is-unread");
      link.closest(".arena-notification-item")?.classList.remove("is-unread");
      link.closest(".arena-notification-item")?.classList.add("is-read");

      void fetch(readUrl, {
        method: "POST",
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      }).finally(() => {
        if (hasTarget) {
          window.location.href = targetHref;
        }
      });
    } else if (hasTarget) {
      window.location.href = targetHref;
    }
  });
})();
