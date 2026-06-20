// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
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

document.addEventListener("DOMContentLoaded", () => {
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
