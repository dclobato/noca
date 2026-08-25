// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// The two behaviours the judgment-data pages need from the browser.
//
// Everything on those pages is an ordinary form that posts immediately, so there
// is no client-side model to maintain -- only two courtesies:
//
// 1. `data-clears-typed-rows` on a file input warns when uploading would discard
//    test-case rows the author typed but has not saved. Those rows are the one
//    piece of state the server has not seen, so they are the only thing an
//    upload can cost.
// 2. A per-row `data-tc-replace-trigger` opens its row's hidden file input, and
//    choosing a file submits that row's form: replacing one case offline is a
//    single decision and should not need a second click.
//
// Both are declared in markup rather than wired per page, and both are delegated
// on the document so a fragment swapped in by the reorder endpoint keeps them.
//
// `data-confirm` is NOT one of these anymore: that listener moved to
// `confirm-submit.js`, which both modules' `_base.html` load on every page.
// Keeping a copy here as well would ask the same question twice on these pages.

(() => {
  "use strict";

  const typedRowCount = () => {
    const container = document.getElementById("tc-add-rows") || document.getElementById("si-add-rows");
    if (!container) return 0;
    return container.querySelectorAll("textarea").length ? container.children.length : 0;
  };

  const discardTypedRows = () => {
    ["tc-add-rows", "si-add-rows"].forEach((id) => {
      const container = document.getElementById(id);
      if (container) container.replaceChildren();
    });
  };

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-tc-replace-trigger]");
    if (!trigger) return;
    const input = trigger.closest("form")?.querySelector("input[data-tc-replace-input]");
    if (input) input.click();
  });

  document.addEventListener("change", (event) => {
    const input = event.target.closest("input[data-tc-replace-input]");
    if (!input || !input.files || input.files.length === 0) return;
    // `requestSubmit` rather than `submit` so the unsaved-rows guard, which
    // listens for submit events, still gets its say.
    input.form?.requestSubmit();
  });

  document.addEventListener("change", (event) => {
    const input = event.target.closest("input[type=file][data-clears-typed-rows]");
    if (!input || !input.files || input.files.length === 0) return;
    const typed = typedRowCount();
    if (typed === 0) return;

    const message =
      `Uploading will discard the ${typed} test case(s) you typed but have not saved. ` +
      "Save them first, or continue and lose them.";
    if (window.confirm(message)) {
      discardTypedRows();
      return;
    }
    // Cancelled: drop the selection so the form cannot be submitted by accident.
    input.value = "";
    if (input.files && input.files.length) {
      const clone = input.cloneNode(true);
      clone.value = "";
      input.parentNode.replaceChild(clone, input);
    }
  });
})();
