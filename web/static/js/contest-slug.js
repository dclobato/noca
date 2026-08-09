// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Auto-generate login_slug from contest_name (lowercase, replace spaces/specials with hyphens).
// No stop-word filtering: the organizer is choosing a URL, so every word is kept.
// Requires shared/static/js/slugify.js to be loaded first.
(function () {
  const nameInput = document.getElementById('contest_name');
  const slugInput = document.getElementById('login_slug');
  if (!nameInput || !slugInput) return;
  nameInput.addEventListener('input', function () {
    if (slugInput.dataset.userEdited) return;
    slugInput.value = window.NocaSlug.slugify(nameInput.value);
  });
  slugInput.addEventListener('input', function () {
    slugInput.dataset.userEdited = '1';
  });
})();
