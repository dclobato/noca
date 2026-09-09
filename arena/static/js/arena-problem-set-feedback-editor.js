// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/** Mount the shared Markdown editor on the teacher's problem-set feedback form. */
(function () {
  'use strict';

  if (!window.NocaStatementEditor) return;

  document.addEventListener('DOMContentLoaded', function () {
    var editor = window.NocaStatementEditor.create({
      textareaId: 'problem-set-feedback-editor',
      changeEventName: null,
      allowLinks: true
    });
    var form = document.getElementById('problem-set-feedback-form');
    if (!form) return;
    form.addEventListener('submit', function () { editor.syncToTextarea(); });
  });
})();
