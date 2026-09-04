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
 *
 * A restored browser draft (`noca-form-draft.js`) writes the textareas directly,
 * so both visible editors are refreshed from them on `noca:form-draft-restored`.
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

    form.addEventListener('noca:form-draft-restored', function () {
      var statement = document.getElementById('stmt-md-editor');
      var editorial = document.getElementById('editorial-md-editor');
      if (statement) statementEditor.setValue(statement.value);
      if (editorial) editorialEditor.setValue(editorial.value);
    });
  });
})();
