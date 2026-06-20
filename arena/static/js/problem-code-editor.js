/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/**
 * Problem detail Ace source-code editor.
 *
 * Mounts a self-hosted Ace editor on top of the hidden #code-area textarea so
 * the plain-POST submit form keeps working.  Syntax highlighting tracks the
 * language chosen in #language-select; changing the language asks for
 * confirmation before re-applying the new Ace mode, and reverts the combobox
 * when the user cancels.  When the change is confirmed (and on initial load
 * with no prefilled source) the editor is filled with the selected language's
 * default starter stub.
 */

document.addEventListener("DOMContentLoaded", function () {
  "use strict";

  if (typeof ace === "undefined") {
    return;
  }

  const editorEl = document.getElementById("code-editor");
  const codeArea = document.getElementById("code-area");
  const langSelect = document.getElementById("language-select");

  if (!editorEl || !codeArea || !langSelect) {
    return;
  }

  // Lazily load Ace mode/theme modules from the local vendor folder.
  const basePath = editorEl.dataset.aceBase;
  if (basePath) {
    ace.config.set("basePath", basePath);
  }
  ace.config.set("useWorker", false);

  const editor = ace.edit(editorEl);
  editor.setTheme("ace/theme/github");
  editor.setOptions({
    showFoldWidgets: true,
    displayIndentGuides: true,
  });

  const selectedOption = function () {
    return langSelect.options[langSelect.selectedIndex] || null;
  };

  const modeForSelectedOption = function () {
    const option = selectedOption();
    return option ? option.dataset.aceMode || "text" : "text";
  };

  const stubForSelectedOption = function () {
    const option = selectedOption();
    return option ? option.dataset.stub || "" : "";
  };

  const applyMode = function (mode) {
    editor.session.setMode("ace/mode/" + mode);
  };

  // Seed the editor: prefilled source (e.g. "continue submission") wins;
  // otherwise start from the selected language's default stub.
  editor.session.setValue(codeArea.value || stubForSelectedOption(), -1);

  // Initial highlighting + keep the hidden textarea in sync for form POST.
  applyMode(modeForSelectedOption());
  codeArea.value = editor.getValue();

  // Editor status bar (line, column, total lines, byte size).
  const statusEl = document.getElementById("code-editor-status");
  const encoder = typeof TextEncoder !== "undefined" ? new TextEncoder() : null;

  const updateStatus = function () {
    if (!statusEl) {
      return;
    }
    const pos = editor.getCursorPosition();
    const text = editor.getValue();
    const lines = editor.session.getLength();
    const bytes = encoder ? encoder.encode(text).length : text.length;
    statusEl.textContent =
      "Ln " +
      (pos.row + 1) +
      ", Col " +
      (pos.column + 1) +
      "  ·  " +
      lines +
      " line" +
      (lines !== 1 ? "s" : "") +
      "  ·  " +
      bytes.toLocaleString() +
      " B";
  };

  updateStatus();
  editor.session.on("change", function () {
    codeArea.value = editor.getValue();
    updateStatus();
  });
  editor.selection.on("changeCursor", updateStatus);
  document.addEventListener("arena:problem-layout-resize", function () {
    editor.resize();
  });

  // Language-change confirmation.
  const modalEl = document.getElementById("language-change-confirm-modal");
  const targetEl = document.getElementById("language-change-target");
  const confirmBtn = document.getElementById("language-change-confirm-btn");

  let previousValue = langSelect.value;
  // Track the stub of the currently selected language so we can detect whether
  // the user has written anything meaningful beyond the default boilerplate.
  let previousStub = stubForSelectedOption();

  const applySelectedLanguage = function () {
    applyMode(modeForSelectedOption());
    editor.session.setValue(stubForSelectedOption(), -1);
    editor.focus();
  };

  const editorIsUnmodified = function () {
    const current = editor.getValue();
    return current === "" || current === previousStub;
  };

  if (!modalEl || !targetEl || !confirmBtn) {
    // Without the modal we still track changes so highlighting stays correct.
    langSelect.addEventListener("change", function () {
      previousStub = stubForSelectedOption();
      previousValue = langSelect.value;
      applySelectedLanguage();
    });
    return;
  }

  const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
  let pendingValue = null;
  let confirmed = false;

  langSelect.addEventListener("change", function () {
    pendingValue = langSelect.value;
    const option = langSelect.options[langSelect.selectedIndex];

    // Skip the confirmation modal when the editor only contains the previous
    // language's boilerplate — there is nothing meaningful to lose.
    if (editorIsUnmodified()) {
      previousStub = option ? option.dataset.stub || "" : "";
      previousValue = pendingValue;
      applySelectedLanguage();
      pendingValue = null;
      return;
    }

    targetEl.textContent = option ? option.text : "";
    modal.show();
  });

  confirmBtn.addEventListener("click", function () {
    confirmed = true;
    previousValue = pendingValue;
    previousStub = stubForSelectedOption();
    // Confirmed change: re-highlight and replace the code with the new stub.
    applySelectedLanguage();
    modal.hide();
  });

  // Revert the combobox if the modal was dismissed without confirming.
  modalEl.addEventListener("hidden.bs.modal", function () {
    if (!confirmed && pendingValue !== null) {
      langSelect.value = previousValue;
    }
    confirmed = false;
    pendingValue = null;
  });
});
