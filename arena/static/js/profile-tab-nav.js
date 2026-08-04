// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Profile tab navigation — replaces Bootstrap client-side tab toggling with
 * full-page URL navigation so that only the active tab's data is fetched from
 * the server. Each button carrying a `data-profile-tab` attribute navigates
 * to a new URL that sets `tab=<value>` while preserving any non-tab-specific
 * query parameters (e.g. the admin list's search/page/per_page/role context).
 * Tab-specific pagination params are cleared on every tab switch to avoid
 * stale page overrides on the new tab.
 */

const TAB_PARAMS = [
  "solved_page",
  "attempted_page",
  "favorite_page",
  "notifications_page",
  "submissions_page",
  "submissions_search",
  "submissions_verdict",
  "credits_page",
  "login_page",
  "login_per_page",
  "login_sort_dir",
  "login_date_from",
  "login_date_to",
];

const PROFILE_NAV_STORAGE_KEY = "noca-arena-profile-sidebar-collapsed";

const readCollapsedPreference = () => {
  try {
    return window.localStorage.getItem(PROFILE_NAV_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
};

const writeCollapsedPreference = (collapsed) => {
  try {
    window.localStorage.setItem(PROFILE_NAV_STORAGE_KEY, String(collapsed));
  } catch {
    // Navigation must keep working when browser storage is unavailable.
  }
};

const setupProfileNavigationCollapse = () => {
  document.querySelectorAll("[data-profile-workspace]").forEach((workspace) => {
    const toggle = workspace.querySelector("[data-profile-navigation-toggle]");
    const tabs = workspace.querySelectorAll("[data-profile-tab]");

    if (!toggle) {
      return;
    }

    const icon = toggle.querySelector(".arena-profile-navigation-toggle-icon");

    const applyState = (collapsed) => {
      workspace.classList.toggle("is-profile-navigation-collapsed", collapsed);
      toggle.setAttribute("aria-expanded", String(!collapsed));
      toggle.setAttribute(
        "aria-label",
        collapsed ? "Expand profile sections" : "Collapse profile sections",
      );

      if (icon) {
        icon.textContent = collapsed ? "arrow_circle_right" : "arrow_circle_left";
      }

      tabs.forEach((tab) => {
        const label = tab.querySelector("span:not(.material-symbols-outlined)");
        if (collapsed && label) {
          tab.title = label.textContent.trim();
        } else {
          tab.removeAttribute("title");
        }
      });
    };

    applyState(readCollapsedPreference());

    toggle.addEventListener("click", () => {
      const collapsed = !workspace.classList.contains("is-profile-navigation-collapsed");
      writeCollapsedPreference(collapsed);
      applyState(collapsed);
    });
  });
};

document.addEventListener("DOMContentLoaded", () => {
  setupProfileNavigationCollapse();

  document.querySelectorAll("[data-profile-tab]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopImmediatePropagation();
      const params = new URLSearchParams(window.location.search);
      TAB_PARAMS.forEach((p) => params.delete(p));
      params.set("tab", btn.dataset.profileTab);
      window.location.href = `?${params.toString()}`;
    });
  });
});
