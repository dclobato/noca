// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Announcement body editor, shared by the Web and Arena admin forms.
 *
 * Mounts the statement editor core on `#announcement-body-editor` with one
 * difference from a problem statement: external links are allowed, so the
 * toolbar carries EasyMDE's link action. Everything else -- the restricted
 * toolbar, the preview through the shared Markdown pipeline -- is the core's.
 * The textarea is synced on submit so the form posts what the author sees.
 */
(function () {
  'use strict';

  if (!window.NocaStatementEditor) return;

  document.addEventListener('DOMContentLoaded', function () {
    var editor = window.NocaStatementEditor.create({
      textareaId: 'announcement-body-editor',
      changeEventName: null,
      allowLinks: true
    });

    var form = document.getElementById('announcement-form');
    if (!form) return;

    form.addEventListener('submit', function () {
      editor.syncToTextarea();
    });
  });
})();
