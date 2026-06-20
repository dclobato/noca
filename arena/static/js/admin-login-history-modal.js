/*
  NOCA -- Next Online Contest Administrator
  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
*/

/**
 * Populates the login-detail modal fields from data attributes on the
 * triggering button.  Each "Details" button carries:
 *   data-login-date, data-login-ip, data-login-location,
 *   data-login-ua, data-login-mode
 */
document.addEventListener("DOMContentLoaded", () => {
  const modal = document.getElementById("login-detail-modal");
  if (!modal) return;

  modal.addEventListener("show.bs.modal", (event) => {
    const btn = event.relatedTarget;
    if (!btn) return;

    const set = (id, value) => {
      const el = modal.querySelector(`#${id}`);
      if (el) el.textContent = value || "—";
    };

    set("login-detail-date",     btn.dataset.loginDate);
    set("login-detail-ip",       btn.dataset.loginIp);
    set("login-detail-location", btn.dataset.loginLocation);
    set("login-detail-ua",       btn.dataset.loginUa);
    set("login-detail-mode",     btn.dataset.loginMode);
  });
});
