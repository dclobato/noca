// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Arena problem statement editor.
 *
 * Thin wrapper over the shared statement-editor core: builds the EasyMDE editor
 * and syncs its value into the underlying textarea on form submit. The Arena form
 * only offers the Markdown editor (no PDF/MD source switching).
 */
(function () {
  'use strict';

  if (!window.NocaStatementEditor) return;

  document.addEventListener('DOMContentLoaded', function () {
    var editor = window.NocaStatementEditor.create();

    var form = document.getElementById('edit-form');
    if (!form) return;

    form.addEventListener('submit', function () {
      editor.syncToTextarea();
    });
  });
})();
