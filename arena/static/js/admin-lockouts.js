//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

(function () {
  "use strict";

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-lockout-fill-target]");
    if (!button) {
      return;
    }

    var input = document.getElementById(button.dataset.lockoutFillTarget);
    if (!input) {
      return;
    }

    input.value = button.dataset.lockoutFillValue || "";
    input.dispatchEvent(new Event("input", { bubbles: true }));

    var clearTargetId = button.dataset.lockoutClearTarget;
    var clearTarget = clearTargetId ? document.getElementById(clearTargetId) : null;
    if (clearTarget) {
      clearTarget.value = "";
      var hashHelp = document.querySelector("[data-lockout-hash-help]");
      if (hashHelp) {
        hashHelp.hidden = true;
      }
    }

    input.focus();
  });
})();
