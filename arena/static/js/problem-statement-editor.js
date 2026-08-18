// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Arena problem Markdown editors.
 *
 * Builds the statement and optional editorial with the same shared EasyMDE core,
 * then syncs both underlying textareas on form submit. Arena offers no PDF/MD
 * source switching.
 */
(function () {
  'use strict';

  if (!window.NocaStatementEditor) return;

  document.addEventListener('DOMContentLoaded', function () {
    var statementEditor = window.NocaStatementEditor.create();
    var editorialEditor = window.NocaStatementEditor.create({
      textareaId: 'editorial-md-editor',
      changeEventName: 'noca:problem-editorial-changed'
    });

    var form = document.getElementById('edit-form');
    if (!form) return;

    form.addEventListener('submit', function () {
      statementEditor.syncToTextarea();
      editorialEditor.syncToTextarea();
    });
  });
})();
