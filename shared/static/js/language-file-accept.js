// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/** Keep source-file pickers aligned with their selected language. */
(function () {
  "use strict";

  document.querySelectorAll("[data-language-file-accept]").forEach(function (fileInput) {
    var select = document.getElementById(fileInput.dataset.languageSelect || "");
    var extensionsElement = document.getElementById(fileInput.dataset.languageExtensions || "");
    if (!select || !extensionsElement) return;

    var extensionMap;
    try {
      extensionMap = JSON.parse(extensionsElement.textContent);
    } catch (_error) {
      return;
    }

    function applyAccept() {
      var extension = extensionMap[select.value];
      if (extension) {
        fileInput.setAttribute("accept", extension);
      } else {
        fileInput.removeAttribute("accept");
      }
    }

    select.addEventListener("change", applyAccept);
    applyAccept();
  });
})();
