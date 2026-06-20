// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

(function () {
  const STORAGE_KEY = "noca-theme";
  const THEME_LIGHT = "light";
  const THEME_DARK = "dark";
  const DEFAULT_THEME = THEME_LIGHT;

  function getTheme() {
    return localStorage.getItem(STORAGE_KEY) || DEFAULT_THEME;
  }

  function setTheme(theme) {
    localStorage.setItem(STORAGE_KEY, theme);
    document.documentElement.setAttribute("data-bs-theme", theme);
  }

  function applyTheme() {
    setTheme(getTheme());
  }

  function toggleTheme() {
    const current = getTheme();
    setTheme(current === THEME_LIGHT ? THEME_DARK : THEME_LIGHT);
    updateToggleButton();
  }

  function updateToggleButton() {
    const btn = document.getElementById("theme-toggle-btn");
    if (!btn) return;
    const isDark = getTheme() === THEME_DARK;
    btn.innerHTML = isDark
      ? '<i class="material-symbols-outlined">light_mode</i>'
      : '<i class="material-symbols-outlined">dark_mode</i>';
    btn.setAttribute(
      "aria-label",
      isDark ? "Switch to light mode" : "Switch to dark mode"
    );
  }

  document.addEventListener("DOMContentLoaded", function () {
    applyTheme();
    updateToggleButton();

    const btn = document.getElementById("theme-toggle-btn");
    if (btn) {
      btn.addEventListener("click", toggleTheme);
    }
  });
})();