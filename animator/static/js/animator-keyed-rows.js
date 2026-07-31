//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Shared keyed reconciliation for Animator table bodies. Renderers own the
// content of a row; this module owns stable identity, authoritative ordering,
// insertion, and removal. Exported as UMD: `window.AnimatorKeyedRows` /
// `module.exports`.
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.AnimatorKeyedRows = api;
  }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  function reconcile(container, items, options) {
    var rows = Array.isArray(items) ? items : [];
    var existingByKey = Object.create(null);
    Array.prototype.slice.call(container.children).forEach(function (row) {
      var key = row.getAttribute("data-team-id");
      if (key !== null) {
        existingByKey[key] = row;
      }
    });

    var seen = Object.create(null);
    rows.forEach(function (item, index) {
      var key = String(options.key(item));
      var row = existingByKey[key];
      seen[key] = true;
      if (row) {
        options.update(row, item, index);
      } else {
        row = options.build(item, index);
      }
      row.setAttribute("data-team-id", key);
      // Re-appending an existing child moves it without replacing the element.
      container.appendChild(row);
    });

    Object.keys(existingByKey).forEach(function (key) {
      if (!seen[key]) {
        container.removeChild(existingByKey[key]);
      }
    });
    return rows.length;
  }

  return { reconcile: reconcile };
});
