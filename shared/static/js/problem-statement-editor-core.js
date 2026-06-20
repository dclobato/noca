// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
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
        'code', 'horizontal-rule', 'preview'
      ],
      spellChecker: false,
      status: ['lines', 'words', 'cursor'],
      tabSize: 4,
      autosave: { enabled: false },
      previewRender: function (plainText, preview) {
        var html = this.parent.markdown(plainText);
        setTimeout(function () { renderLatex(preview); }, 0);
        return html;
      }
    });

    var editor = new StatementEditor(mde);
    mde.codemirror.on('change', function () { editor.notifyChanged(); });
    return editor;
  }

  window.NocaStatementEditor = { create: create, renderLatex: renderLatex };
})();
