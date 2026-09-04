// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Mandatory-announcement pop-up.
 *
 * Opens `#announcement-required-modal` as soon as the page is ready and keeps
 * the Acknowledge button disabled until the checkbox is ticked. The modal's
 * static backdrop and disabled keyboard dismissal are declared on the element;
 * this script only owns the open-on-load and the checkbox-to-button coupling.
 * The server re-checks the acknowledgment on every page, so nothing here is a
 * security boundary.
 */
(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    var modal = document.getElementById('announcement-required-modal');
    if (!modal || typeof bootstrap === 'undefined') return;

    var checkbox = document.getElementById('announcement-acknowledge');
    var submit = document.getElementById('announcement-acknowledge-submit');
    if (checkbox && submit) {
      submit.disabled = !checkbox.checked;
      checkbox.addEventListener('change', function () {
        submit.disabled = !checkbox.checked;
      });
    }

    bootstrap.Modal.getOrCreateInstance(modal).show();
  });
})();
