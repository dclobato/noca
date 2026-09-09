/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 The NOCA Authors (see AUTHORS)
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/*
 * Slug policy for Arena's flat taxonomies -- categories and collections -- used
 * by admin-taxonomy-list.js and admin-taxonomy-form.js.
 *
 * The mechanical transform lives in shared/static/js/slugify.js; this file adds
 * only the stop-word set.  The preview must agree with what the server stores,
 * so the set mirrors SLUG_STOP_WORDS / normalize_slug() in
 * arena/services/taxonomy_validation.py.  Keep the two in sync: a divergence
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

  window.NocaTaxonomySlug = { slugify };
})();
