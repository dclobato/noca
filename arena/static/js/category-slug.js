/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 The NOCA Authors (see AUTHORS)
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/*
 * Arena category slug policy, used by both Arena category forms
 * (admin-category-list.js and admin-category-form.js).
 *
 * The mechanical transform lives in shared/static/js/slugify.js; this file adds
 * only the stop-word set.  The preview must agree with what the server stores,
 * so the set mirrors _SLUG_STOP_WORDS / normalize_slug() in
 * arena/services/admin_category_service.py.  Keep the two in sync: a divergence
 * shows the author one slug and saves another.
 *
 * Requires shared/static/js/slugify.js to be loaded first.
 */
(() => {
  "use strict";

  const SLUG_STOP_WORDS = new Set([
    // Portuguese articles
    "a", "o", "as", "os", "um", "uma",
    // Portuguese prepositions & contractions
    "de", "do", "da", "dos", "das",
    "em", "no", "na", "nos", "nas",
    "por", "para", "com",
    "pelo", "pela", "pelos", "pelas",
    // Portuguese conjunctions / pronouns
    "e", "ou", "se",
    // English articles / prepositions / conjunctions
    "the", "an", "and", "or",
    "of", "in", "on", "for", "to", "from", "with", "by", "at",
    // English copula
    "is", "are",
  ]);

  const slugify = (value) =>
    window.NocaSlug?.slugify(value, { stopWords: SLUG_STOP_WORDS }) ?? String(value ?? "");

  window.NocaCategorySlug = { slugify };
})();
