/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/**
 * Languages help page: activate the tab containing a hash target before scrolling.
 */

document.addEventListener("DOMContentLoaded", () => {
  "use strict";

  function hashTarget(hash) {
    if (!hash || hash === "#") {
      return null;
    }

    try {
      return document.getElementById(decodeURIComponent(hash.slice(1)));
    } catch (_err) {
      return null;
    }
  }

  function tabButtonForPane(pane) {
    return Array.from(document.querySelectorAll('[data-bs-toggle="tab"]')).find(
      (button) => button.getAttribute("data-bs-target") === `#${pane.id}`,
    );
  }

  function scrollToTarget(target) {
    target.scrollIntoView({ block: "start" });
  }

  function showTabForHash(hash) {
    const target = hashTarget(hash);
    if (!target) {
      return;
    }

    const pane = target.closest(".tab-pane");
    if (!pane || pane.classList.contains("active") || typeof bootstrap === "undefined") {
      scrollToTarget(target);
      return;
    }

    const button = tabButtonForPane(pane);
    if (!button) {
      return;
    }

    button.addEventListener("shown.bs.tab", () => scrollToTarget(target), { once: true });
    bootstrap.Tab.getOrCreateInstance(button).show();
  }

  showTabForHash(window.location.hash);
  window.addEventListener("hashchange", () => showTabForHash(window.location.hash));
});
