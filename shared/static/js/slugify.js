/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 The NOCA Authors (see AUTHORS)
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/*
 * URL-slug generation shared by the Contest and Arena modules.
 *
 * This owns the mechanical transform only -- lowercase, strip diacritics, and
 * join the remaining alphanumeric runs with hyphens.  Which words survive that
 * transform is a per-surface policy passed in through `stopWords`, because the
 * modules deliberately disagree:
 *
 *   - Contest login slugs (web/static/js/contest-slug.js) keep every word: the
 *     organizer is choosing a URL and it must not be rewritten under them.
 *   - Arena category slugs (arena/static/js/category-slug.js) drop articles and
 *     prepositions, mirroring normalize_slug() in the Arena category service.
 */
(() => {
  "use strict";

  /**
   * Build a URL slug from arbitrary text.
   *
   * @param {string} value Raw text to slugify.
   * @param {{stopWords?: Set<string>}} [options] Words to drop from the result.
   * @returns {string} Hyphen-joined slug, possibly empty.
   */
  const slugify = (value, options = {}) => {
    const { stopWords } = options;
    return String(value ?? "")
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-z0-9]+/g, " ")
      .trim()
      .split(" ")
      .filter((word) => word && !stopWords?.has(word))
      .join("-");
  };

  window.NocaSlug = { slugify };
})();
