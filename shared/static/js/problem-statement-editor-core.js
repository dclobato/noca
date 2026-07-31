// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Shared statement-editor core for the web and arena problem create/edit forms.
 *
 * Owns the parts that are identical between both modules: KaTeX preview
 * rendering, the EasyMDE instance with its restricted toolbar, the
 * `noca:problem-statement-changed` notification, and value/enabled/sync helpers.
 *
 * Exposes `window.NocaStatementEditor.create()`, which builds the EasyMDE editor
 * on `#stmt-md-editor` and returns a `StatementEditor` instance. When EasyMDE or
 * the textarea is missing, the returned instance wraps a null editor and all
 * methods degrade gracefully. Each module supplies only its own surrounding
 * logic (arena: submit sync; web: PDF/MD source switching).
 */
(function () {
  'use strict';

  function renderLatex(preview) {
    if (!preview || typeof renderMathInElement !== 'function') return;
    renderMathInElement(preview, {
      delimiters: [
        { left: '$$', right: '$$', display: true },
        { left: '$',  right: '$',  display: false },
        { left: '\\(', right: '\\)', display: false },
        { left: '\\[', right: '\\]', display: true }
      ],
      throwOnError: false,
      ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code', 'svg']
    });
  }

  function StatementEditor(mde) {
    this.mde = mde;
  }

  var DEFAULT_TABLE_MARKDOWN = [
    '::: table-border on',
    '::: table-align left',
    '',
    '| Column 1 | Column 2 | Column 3 |',
    '| -------- | -------- | -------- |',
    '| Text     | Text     | Text     |'
  ].join('\n');

  /**
   * Insert a starter table with NOCA's explicit default presentation directives.
   *
   * @param {EasyMDE} mde Active Markdown editor.
   */
  function insertTable(mde) {
    var codemirror = mde.codemirror;
    var from = codemirror.getCursor('from');
    var to = codemirror.getCursor('to');
    var lastLine = codemirror.lastLine();
    var documentEnd = { line: lastLine, ch: codemirror.getLine(lastLine).length };
    var before = codemirror.getRange({ line: 0, ch: 0 }, from);
    var after = codemirror.getRange(to, documentEnd);
    var prefix = '';
    var suffix = '';

    if (before && !before.endsWith('\n\n')) {
      prefix = before.endsWith('\n') ? '\n' : '\n\n';
    }
    if (after && !after.startsWith('\n\n')) {
      suffix = after.startsWith('\n') ? '\n' : '\n\n';
    }

    codemirror.replaceSelection(prefix + DEFAULT_TABLE_MARKDOWN + suffix);
    codemirror.focus();
  }

  /** Build the EasyMDE toolbar control for a starter table. */
  function tableButton() {
    return {
      name: 'noca-table',
      action: insertTable,
      className: 'fa fa-table',
      title: 'Insert table (bordered, left-aligned)'
    };
  }

  /**
   * Insert or replace the alignment directive for the paragraph at the cursor.
   *
   * @param {EasyMDE} mde Active Markdown editor.
   * @param {'left'|'center'|'right'} alignment Requested paragraph alignment.
   */
  function insertParagraphAlignment(mde, alignment) {
    var codemirror = mde.codemirror;
    var cursor = codemirror.getCursor('from');
    var paragraphStart = cursor.line;

    while (
      paragraphStart > 0
      && codemirror.getLine(paragraphStart).trim() !== ''
      && codemirror.getLine(paragraphStart - 1).trim() !== ''
    ) {
      paragraphStart -= 1;
    }

    var directive = '::: align ' + alignment;
    var currentLine = codemirror.getLine(paragraphStart);
    if (/^:::\s+align\s+(left|center|right)\s*$/.test(currentLine.trim())) {
      codemirror.replaceRange(
        directive,
        { line: paragraphStart, ch: 0 },
        { line: paragraphStart, ch: currentLine.length }
      );
    } else if (
      paragraphStart >= 2
      && codemirror.getLine(paragraphStart - 1).trim() === ''
      && /^:::\s+align\s+(left|center|right)\s*$/.test(
        codemirror.getLine(paragraphStart - 2).trim()
      )
    ) {
      var existingLine = codemirror.getLine(paragraphStart - 2);
      codemirror.replaceRange(
        directive,
        { line: paragraphStart - 2, ch: 0 },
        { line: paragraphStart - 2, ch: existingLine.length }
      );
    } else {
      codemirror.replaceRange(directive + '\n\n', { line: paragraphStart, ch: 0 });
    }
    codemirror.focus();
  }

  /** Build one EasyMDE toolbar control for paragraph alignment. */
  function alignmentButton(alignment) {
    return {
      name: 'noca-align-' + alignment,
      action: function (mde) {
        insertParagraphAlignment(mde, alignment);
      },
      className: 'fa fa-align-' + alignment,
      title: 'Align paragraph ' + alignment
    };
  }

  StatementEditor.prototype.value = function () {
    if (this.mde) return this.mde.value();
    var ta = document.getElementById('stmt-md-editor');
    return ta ? ta.value : '';
  };

  StatementEditor.prototype.setValue = function (text) {
    if (this.mde) this.mde.value(text || '');
  };

  StatementEditor.prototype.notifyChanged = function () {
    document.dispatchEvent(new CustomEvent('noca:problem-statement-changed', {
      detail: { value: this.value() }
    }));
  };

  StatementEditor.prototype.syncToTextarea = function () {
    if (!this.mde) return;
    var ta = document.getElementById('stmt-md-editor');
    if (ta) ta.value = this.mde.value();
  };

  StatementEditor.prototype.setEnabled = function (enabled) {
    if (!this.mde) return;
    var cm = this.mde.codemirror;
    if (enabled) {
      cm.setOption('readOnly', false);
      cm.getWrapperElement().classList.remove('noca-editor-disabled');
    } else {
      cm.setOption('readOnly', 'nocursor');
      cm.getWrapperElement().classList.add('noca-editor-disabled');
    }
  };

  // EasyMDE — restricted toolbar (text + formatting only; no link/image)
  function create() {
    var textarea = document.getElementById('stmt-md-editor');
    if (!textarea || typeof EasyMDE === 'undefined') return new StatementEditor(null);

    var mde = new EasyMDE({
      element: textarea,
      autoDownloadFontAwesome: false,
      indentWithTabs: false,
      toolbar: [
        'bold', 'italic', 'heading', '|',
        'quote', 'unordered-list', 'ordered-list', '|',
        'code', 'horizontal-rule', '|',
        tableButton(), alignmentButton('left'), alignmentButton('center'),
        alignmentButton('right'), '|',
        'preview'
      ],
      spellChecker: false,
      status: ['lines', 'words', 'cursor'],
      tabSize: 4,
      autosave: { enabled: false },
      previewRender: function (plainText, preview) {
        var markdown = window.NocaMarkdownDirectives
          ? window.NocaMarkdownDirectives.prepareMarkdown(plainText)
          : plainText;
        var html = this.parent.markdown(markdown);
        setTimeout(function () { renderLatex(preview); }, 0);
        return html;
      }
    });

    var editor = new StatementEditor(mde);
    mde.codemirror.on('change', function () { editor.notifyChanged(); });
    return editor;
  }

  window.NocaStatementEditor = {
    create: create,
    insertTable: insertTable,
    insertParagraphAlignment: insertParagraphAlignment,
    renderLatex: renderLatex
  };
})();
