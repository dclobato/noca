// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Auto-generate login_slug from contest_name (lowercase, replace spaces/specials with hyphens)
(function () {
  const nameInput = document.getElementById('contest_name');
  const slugInput = document.getElementById('login_slug');
  if (!nameInput || !slugInput) return;
  nameInput.addEventListener('input', function () {
    if (slugInput.dataset.userEdited) return;
    slugInput.value = nameInput.value
      .toLowerCase()
      .normalize('NFD').replace(/[\u0300-\u036f]/g, '')  // strip diacritics
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '');
  });
  slugInput.addEventListener('input', function () {
    slugInput.dataset.userEdited = '1';
  });
})();
