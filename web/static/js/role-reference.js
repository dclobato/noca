//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Highlights the role reference matrices to match the role currently selected
// in the add-user form: the matching row of the access matrix, and the matching
// column of the capability matrix.
//
// This is progressive enhancement only. With JavaScript off, both tables render
// complete and readable; nothing here is a permission check.

(function () {
  "use strict";

  var HIGHLIGHT_CLASS = "noca-role-matrix-current";

  function clearHighlight(root) {
    root.querySelectorAll("." + HIGHLIGHT_CLASS).forEach(function (cell) {
      cell.classList.remove(HIGHLIGHT_CLASS);
    });
  }

  function applyHighlight(root, role) {
    clearHighlight(root);
    if (!role) {
      return;
    }
    // The access matrix keys its rows by actor and the capability matrix keys
    // its cells by actor, so one selector serves both shapes.
    var selector = '[data-noca-actor="' + role.replace(/"/g, "") + '"]';
    root.querySelectorAll(selector).forEach(function (element) {
      element.classList.add(HIGHLIGHT_CLASS);
    });
  }

  function init() {
    var reference = document.getElementById("role-reference");
    var select = document.getElementById("role");
    if (!reference || !select) {
      return;
    }
    var update = function () {
      applyHighlight(reference, select.value);
    };
    select.addEventListener("change", update);
    // Re-render of a submitted form keeps the chosen role selected, so honour
    // it on load rather than only on the next change.
    update();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
