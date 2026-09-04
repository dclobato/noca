//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Free-text suggestion comboboxes for the Arena problem form (Source, Author,
// License), built on the shared `.arena-combo` controller.
//
// These deliberately render their own listbox instead of using a native
// `<datalist>`. A datalist applies the browser's own substring filter on top of
// the options it is given, so a server answer that matched out of order --
// "cutigi carlos" finding "Jorge Francisco Cutigi (IFSP, Sao Carlos)" -- was
// silently discarded before it could be shown, and the field looked as if it
// had no suggestions at all. The server's ranking is the only filter.
//
// Markup contract (see `_partials/problem_tab_metadata.html`):
//   <div class="arena-combo">
//     <input data-suggestions-url data-suggestions-field role="combobox"
//            aria-controls="<listbox id>" ...>
//     <div class="arena-combo-dropdown" id="<listbox id>" role="listbox"></div>
//     <div class="visually-hidden" role="status" data-suggest-status></div>
//   </div>
//
// Exported as UMD: `window.ArenaSuggestCombobox` / `module.exports`.
(function (root, factory) {
  "use strict";
  var listbox =
    root && root.ArenaComboListbox
      ? root.ArenaComboListbox
      : typeof require !== "undefined"
        ? require("./arena-combo-listbox.js")
        : null;
  var api = factory(listbox);
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.ArenaSuggestCombobox = api;
    if (typeof document !== "undefined") {
      document.querySelectorAll("input[data-suggestions-url][data-suggestions-field]").forEach(api.setupInput);
    }
  }
})(typeof window !== "undefined" ? window : null, function (comboListbox) {
  "use strict";

  // Every term needs three letters or digits, mirroring the server: a shorter
  // term cannot be answered from a trigram index, so the endpoint declines it
  // rather than serving it by sequential scan. Sending it anyway would only
  // trade a hint for an empty result the author would read as "nothing found".
  var MIN_TERM_LENGTH = 3;
  var WORD_CHARACTER = /[\p{L}\p{Nd}]/gu;
  var DEBOUNCE_MS = 250;

  /** @param {string} query Trimmed query text. */
  function isSearchable(query) {
    var terms = query.split(/\s+/).filter(Boolean);
    return (
      terms.length > 0 &&
      terms.every(function (term) {
        return (term.match(WORD_CHARACTER) || []).length >= MIN_TERM_LENGTH;
      })
    );
  }

  /**
   * Build one suggestion combobox.
   *
   * Deps: { input, listbox, statusEl, url, field } plus the optional
   * { doc, fetchImpl, debounceMs } the Node contract test injects.
   */
  function createSuggestCombobox(deps) {
    var input = deps.input;
    var fetchImpl = deps.fetchImpl || (typeof fetch !== "undefined" ? fetch : null);
    var applying = false;

    var combo = comboListbox.createComboListbox({
      input: input,
      listbox: deps.listbox,
      statusEl: deps.statusEl,
      doc: deps.doc,
      isSearchable: isSearchable,
      tooShortMessage: function (query) {
        if (!query) return null;
        return "Type at least " + MIN_TERM_LENGTH + " letters or digits per word.";
      },
      debounceMs: typeof deps.debounceMs === "number" ? deps.debounceMs : DEBOUNCE_MS,
      optionIdPrefix: (input.id || "suggestion") + "-option",
      closeOnBlur: true,
      loadingMessage: "Loading suggestions…",
      errorMessage: "Could not load suggestions. Try again.",
      emptyMessage: function () {
        return "No matches found.";
      },
      announceCount: function (count) {
        return count + " suggestion" + (count === 1 ? "" : "s") + " available.";
      },
      search: function (query, signal) {
        var params = new URLSearchParams({ field: deps.field, q: query });
        return fetchImpl(deps.url + "?" + params, { signal: signal }).then(function (response) {
          if (!response.ok) throw new Error("Suggestion request failed");
          return response.json().then(function (data) {
            return Array.isArray(data.suggestions)
              ? data.suggestions.filter(function (value) {
                  return typeof value === "string";
                })
              : [];
          });
        });
      },
      shouldIgnoreInput: function () {
        return applying;
      },
      onSelect: function (suggestion) {
        // A programmatic value change fires no events, so the form's validation
        // and its unsaved-changes guard would never see the pick.
        applying = true;
        input.value = suggestion;
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
        applying = false;
        combo.close();
        if (typeof input.focus === "function") input.focus();
      },
    });

    return combo;
  }

  /**
   * Wire one already-rendered input to the listbox in its `.arena-combo`.
   *
   * @param {HTMLInputElement} input Text input carrying the suggestion dataset.
   */
  function setupInput(input) {
    var url = input.getAttribute("data-suggestions-url") || "";
    var field = input.getAttribute("data-suggestions-field") || "";
    var combo = input.closest(".arena-combo");
    var listbox = combo ? combo.querySelector("[role='listbox']") : null;
    if (!url || !field || !listbox) return null;

    return createSuggestCombobox({
      input: input,
      listbox: listbox,
      statusEl: combo.querySelector("[data-suggest-status]"),
      url: url,
      field: field,
    });
  }

  return {
    MIN_TERM_LENGTH: MIN_TERM_LENGTH,
    DEBOUNCE_MS: DEBOUNCE_MS,
    isSearchable: isSearchable,
    createSuggestCombobox: createSuggestCombobox,
    setupInput: setupInput,
  };
});
