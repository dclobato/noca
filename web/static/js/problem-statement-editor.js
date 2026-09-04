// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Web problem Markdown editors.
 *
 * Wraps the shared editor core with Web's PDF/MD statement switching and builds
 * the optional database-backed editorial as a second independent instance.
 * Layout: file upload field (top) + EasyMDE Markdown editor (bottom) — both always
 * visible. When a PDF file is selected, the editor is disabled. When a .md file is
 * selected, its content is loaded into the editor and the file input is cleared.
 * The "Replace with empty Markdown" button (shown only for existing-PDF edit mode)
 * clears the file input, enables the editor empty, and sets statement_source to "md".
 *
 * A restored browser draft (`noca-form-draft.js`) writes the textareas directly,
 * so both visible editors are refreshed from them on `noca:form-draft-restored`.
 * A non-empty restored statement while the editor is disabled (an existing PDF)
 * is what the author typed after "Replace with empty Markdown", so that same
 * transition is replayed first -- otherwise the Save would discard the text.
 */
(function () {
  'use strict';

  if (!window.NocaStatementEditor) return;

  var sentinel = document.getElementById('stmt-state-sentinel');
  if (!sentinel) return;

  var hasPdf  = sentinel.dataset.hasPdf  === 'true';
  var hasMd   = sentinel.dataset.hasMd   === 'true';
  var isEdit  = sentinel.dataset.isEdit  === 'true';

  var sourceInput = document.getElementById('statement_source');
  var fileInput   = document.getElementById('stmt-file-input');
  var replaceBtn  = document.getElementById('stmt-replace-with-md');
  var form        = document.getElementById('edit-form');

  var editor = null;
  var editorialEditor = null;

  function setSource(v) {
    if (sourceInput) sourceInput.value = v;
  }

  document.addEventListener('DOMContentLoaded', function () {
    editor = window.NocaStatementEditor.create();
    editorialEditor = window.NocaStatementEditor.create({
      textareaId: 'editorial-md-editor',
      changeEventName: 'noca:problem-editorial-changed'
    });

    // Initial state
    if (isEdit && hasPdf) {
      // Edit, existing PDF — disable editor, keep "unchanged" until user acts
      setSource('unchanged');
      editor.setEnabled(false);
    } else {
      // New problem, edit with existing MD, or edit with no statement — MD mode
      setSource('md');
      editor.setEnabled(true);
    }

    // File input change handler
    if (fileInput) {
      fileInput.addEventListener('change', function () {
        var file = fileInput.files && fileInput.files[0];
        if (!file) {
          // File cleared — restore mode based on original context
          if (isEdit && hasPdf) {
            setSource('unchanged');
            editor.setEnabled(false);
          } else {
            setSource('md');
            editor.setEnabled(true);
          }
          return;
        }

        var nameLower = file.name.toLowerCase();
        if (nameLower.endsWith('.md')) {
          // Read .md content into editor then clear the file input
          var reader = new FileReader();
          reader.onload = function (evt) {
            editor.setValue(evt.target.result || '');
            // Clear the file input — content is now in the editor
            fileInput.value = '';
            setSource('md');
            editor.setEnabled(true);
            editor.notifyChanged();
          };
          reader.readAsText(file, 'utf-8');
        } else {
          // PDF selected — disable editor, set source to pdf
          setSource('pdf');
          editor.setEnabled(false);
          editor.notifyChanged();
        }
      });
    }

    function switchToMarkdown(text) {
      if (fileInput) fileInput.value = '';
      editor.setValue(text);
      setSource('md');
      editor.setEnabled(true);
      // Hide the button — user is now in MD mode
      if (replaceBtn) replaceBtn.classList.add('d-none');
      editor.notifyChanged();
    }

    // "Replace with empty Markdown" button
    if (replaceBtn) {
      replaceBtn.addEventListener('click', function () { switchToMarkdown(''); });
    }

    if (form) {
      form.addEventListener('noca:form-draft-restored', function () {
        var statement = document.getElementById('stmt-md-editor');
        var editorial = document.getElementById('editorial-md-editor');
        if (statement) {
          var disabled = sourceInput && sourceInput.value !== 'md';
          if (disabled && statement.value) switchToMarkdown(statement.value);
          else editor.setValue(statement.value);
        }
        if (editorial && editorialEditor) editorialEditor.setValue(editorial.value);
      });
    }

    // Form submit — sync EasyMDE value into underlying textarea
    if (form) {
      form.addEventListener('submit', function () {
        if (sourceInput && sourceInput.value === 'md') {
          editor.syncToTextarea();
        }
        if (editorialEditor) editorialEditor.syncToTextarea();
      });
    }
  });
})();
