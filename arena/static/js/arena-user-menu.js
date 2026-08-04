/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 The NOCA Authors (see AUTHORS)
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

(() => {
  "use strict";

  const root = document.querySelector("[data-arena-user-menu]");
  if (!root) {
    return;
  }

  const toggle = root.querySelector("[data-arena-user-menu-toggle]");
  const dropdown = root.querySelector("[data-arena-user-menu-dropdown]");

  if (!toggle || !dropdown) {
    return;
  }

  const setOpen = (open) => {
    dropdown.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
  };

  toggle.addEventListener("click", () => {
    setOpen(dropdown.hidden);
  });

  document.addEventListener("click", (event) => {
    if (!root.contains(event.target)) {
      setOpen(false);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !dropdown.hidden) {
      setOpen(false);
      toggle.focus();
    }
  });
})();
