// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Favorite toggle for the problem detail page.
 *
 * Reads data-toggle-url and data-is-favorite from the button element,
 * POSTs to the toggle endpoint, and updates the icon and aria-label on success.
 */
document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("favorite-toggle-btn");
  if (!btn) return;

  btn.addEventListener("click", async () => {
    const url = btn.dataset.toggleUrl;
    if (!url) return;

    btn.disabled = true;
    try {
      const resp = await fetch(url, {
        method: "POST",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
      });
      if (resp.status === 401) {
        window.location.href = btn.dataset.loginUrl || "/arena/login";
        return;
      }
      if (!resp.ok) return;
      const data = await resp.json();
      _updateButton(btn, data.is_favorite);
    } finally {
      btn.disabled = false;
    }
  });
});

function _updateButton(btn, isFavorite) {
  const icon = btn.querySelector("[class*='material-symbols']");
  if (!icon) return;
  if (isFavorite) {
    icon.classList.add("material-symbols-filled", "arena-favorite-icon--active");
    btn.setAttribute("aria-label", "Remove from favorites");
    btn.setAttribute("title", "Remove from favorites");
  } else {
    icon.classList.remove("material-symbols-filled", "arena-favorite-icon--active");
    btn.setAttribute("aria-label", "Add to favorites");
    btn.setAttribute("title", "Add to favorites");
  }
}
