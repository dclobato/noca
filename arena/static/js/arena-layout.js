/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

(() => {
  "use strict";

  const storageKey = "noca-arena-sidebar-collapsed";
  const collapsedClass = "arena-sidebar-collapsed";
  const mobileOpenClass = "arena-mobile-sidebar-open";
  const toggle = document.querySelector("[data-arena-sidebar-toggle]");
  const icon = document.querySelector("[data-arena-sidebar-toggle-icon]");
  const mobileToggle = document.querySelector("[data-arena-mobile-menu-toggle]");
  const backdrop = document.querySelector("[data-arena-mobile-sidebar-backdrop]");

  if (!toggle || !icon || !mobileToggle || !backdrop) {
    return;
  }

  const applyState = (collapsed) => {
    document.body.classList.toggle(collapsedClass, collapsed);
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
    icon.textContent = collapsed ? "arrow_circle_right" : "arrow_circle_left";
  };

  applyState(window.localStorage.getItem(storageKey) === "true");

  toggle.addEventListener("click", () => {
    const collapsed = !document.body.classList.contains(collapsedClass);
    window.localStorage.setItem(storageKey, String(collapsed));
    applyState(collapsed);
  });

  const setMobileOpen = (open) => {
    document.body.classList.toggle(mobileOpenClass, open);
    mobileToggle.setAttribute("aria-expanded", String(open));
    mobileToggle.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
    backdrop.hidden = !open;
  };

  mobileToggle.addEventListener("click", () => {
    setMobileOpen(!document.body.classList.contains(mobileOpenClass));
  });

  backdrop.addEventListener("click", () => {
    setMobileOpen(false);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      setMobileOpen(false);
    }
  });
})();
