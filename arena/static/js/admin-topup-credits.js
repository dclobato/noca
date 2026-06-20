// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Intercepts the admin topup-credits form submit and asks for browser-level
 * confirmation before sending the POST request.
 */
document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("admin-topup-credits-form");
  if (!form) return;

  form.addEventListener("submit", (e) => {
    const qty = document.getElementById("topup-quantity")?.value || "0";
    const userName = form.dataset.userName || "this user";
    const n = parseInt(qty, 10);
    const label = n === 1 ? "1 credit" : `${n} credits`;
    if (!window.confirm(`Add ${label} to ${userName}?`)) {
      e.preventDefault();
    }
  });
});
