// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

// Makes the quick-submit file picker extension-aware: selecting a language
// constrains the file dialog to that language's source extension (e.g. Java
// shows only *.java). Falls back to accepting any file when no extension maps.
(function () {
  const langSelect = document.getElementById("language_id");
  const fileInput = document.getElementById("source_file");
  const extEl = document.getElementById("quick-submit-lang-ext");
  if (!langSelect || !fileInput || !extEl) return;

  const extMap = JSON.parse(extEl.textContent);

  function applyAccept() {
    const ext = extMap[langSelect.value];
    if (ext) {
      fileInput.setAttribute("accept", ext);
    } else {
      fileInput.removeAttribute("accept");
    }
  }

  langSelect.addEventListener("change", applyAccept);
  applyAccept();
})();
