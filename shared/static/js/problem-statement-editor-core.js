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
 * Exposes `window.NocaStatementEditor.create()`, which builds an EasyMDE editor
 * for a configured textarea and returns a `StatementEditor` instance. Statement
 * and editorial fields therefore use exactly the same toolbar and preview
 * pipeline without sharing state. When EasyMDE or the textarea is missing, the
 * returned instance wraps a null editor and all methods degrade gracefully.
 */
(function () {
  'use strict';

  /**
   * Run the shared post-parse passes (Mermaid, KaTeX, directives) on a preview.
   *
   * EasyMDE owns the preview element and its Markdown parser, so the preview
   * cannot be a `data-noca-markdown` container. Delegating the passes keeps it
   * on the same pipeline as published Markdown instead of a private copy.
   *
   * @param {Element} preview EasyMDE preview element.
   * @returns {void}
   */
  function enhancePreview(preview) {
    if (!preview || !window.NocaMarkdown) return;
    window.NocaMarkdown.enhance(preview);
  }

  function StatementEditor(mde, textarea, changeEventName) {
    this.mde = mde;
    this.textarea = textarea;
    this.changeEventName = changeEventName;
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
    return this.textarea ? this.textarea.value : '';
  };

  StatementEditor.prototype.setValue = function (text) {
    if (this.mde) this.mde.value(text || '');
    else if (this.textarea) this.textarea.value = text || '';
  };

  StatementEditor.prototype.notifyChanged = function () {
    if (!this.changeEventName) return;
    document.dispatchEvent(new CustomEvent(this.changeEventName, {
      detail: { value: this.value() }
    }));
  };

  StatementEditor.prototype.syncToTextarea = function () {
    if (!this.mde) return;
    if (this.textarea) this.textarea.value = this.mde.value();
  };

  /**
   * Re-measure the editor after it becomes visible.
   *
   * CodeMirror computes its layout from the element's box, and a `display:none`
   * tab pane has none -- so an editor built while its pane is hidden renders
   * blank until something forces a reflow (which is why clicking into it used to
   * make the statement "appear"). The text was never lost: the document is read
   * from the textarea at construction, so `value()` was correct all along.
   */
  StatementEditor.prototype.refresh = function () {
    if (this.mde && this.mde.codemirror) this.mde.codemirror.refresh();
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
  function create(options) {
    options = options || {};
    var textareaId = options.textareaId || 'stmt-md-editor';
    var changeEventName = options.changeEventName === undefined
      ? 'noca:problem-statement-changed'
      : options.changeEventName;
    var textarea = document.getElementById(textareaId);
    if (!textarea || typeof EasyMDE === 'undefined') {
      return new StatementEditor(null, textarea, changeEventName);
    }

    var mde = new EasyMDE({
      element: textarea,
      autoDownloadFontAwesome: false,
      forceSync: true,
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
        // Prefer the shared pipeline so the preview matches published output
        // exactly. It needs Marked on the page; a form that has not loaded it
        // falls back to EasyMDE's bundled parser rather than previewing blank.
        var html = window.NocaMarkdown && typeof marked !== 'undefined'
          ? window.NocaMarkdown.toHtml(plainText)
          : this.parent.markdown(
            window.NocaMarkdownDirectives
              ? window.NocaMarkdownDirectives.prepareMarkdown(plainText)
              : plainText
          );
        setTimeout(function () { enhancePreview(preview); }, 0);
        return html;
      }
    });

    var editor = new StatementEditor(mde, textarea, changeEventName);
    mde.codemirror.on('change', function () {
      // `forceSync` updates the hidden form control before this later listener.
      // Surface the change so required-field validity does not retain its
      // page-load state while the author is typing in CodeMirror.
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
      editor.notifyChanged();
    });

    // The problem editor mounts every tab pane at once and lets Bootstrap toggle
    // visibility, so this editor is usually built inside a hidden pane. Re-measure
    // when its pane is shown; harmless on pages that have no tab strip.
    var tabs = document.getElementById('problem-edit-tabs');
    if (tabs) {
      tabs.addEventListener('shown.bs.tab', function () { editor.refresh(); });
    }
    // Also re-measure once after layout settles, for the case where the editor's
    // own pane is the one open on load.
    window.setTimeout(function () { editor.refresh(); }, 0);
    if (textarea.disabled) editor.setEnabled(false);
    return editor;
  }

  // `renderLatex` is intentionally not exported: post-parse passes now belong to
  // the shared pipeline, so callers use `window.NocaMarkdown.enhance()` instead.
  window.NocaStatementEditor = {
    create: create,
    insertTable: insertTable,
    insertParagraphAlignment: insertParagraphAlignment
  };
})();
