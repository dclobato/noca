// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Applies NOCA's one-line Markdown directives to the next rendered block.
 *
 * Marked renders a standalone directive as a paragraph. This module observes
 * published Markdown and EasyMDE previews, converts supported directives into
 * presentation classes, and removes the directive paragraph. Unsupported or
 * misplaced directives remain visible so authors can correct them.
 */
(function () {
  'use strict';

  // Every rendered-Markdown container carries `.noca-markdown` (see
  // noca-markdown.js); EasyMDE owns `.editor-preview` and cannot carry it.
  var CONTAINER_SELECTOR = '.noca-markdown, .editor-preview';
  var DIRECTIVE_PATTERN = /^:::\s+(table-border|table-align|align)\s+([a-z]+)\s*$/;
  var FENCE_PATTERN = /^ {0,3}(`{3,}|~{3,})/;
  var LITERAL_DOLLAR_HTML = '<span class="noca-markdown-literal-dollar">$</span>';
  var ALIGNMENT_CLASSES = [
    'noca-markdown-align-left',
    'noca-markdown-align-center',
    'noca-markdown-align-right'
  ];
  var TABLE_ALIGNMENT_CLASSES = [
    'noca-markdown-table-align-left',
    'noca-markdown-table-align-center',
    'noca-markdown-table-align-right'
  ];

  /**
   * Prepare NOCA extensions before Marked parses the source.
   *
   * Markdown consumes the backslash from `\$` before KaTeX auto-render runs.
   * Isolating the dollar in its own span keeps its normal inherited typography
   * while preventing KaTeX from pairing it with another dollar delimiter. Valid
   * directive lines receive block separation so authors need not add blank lines.
   *
   * @param {string} markdown Raw Markdown source.
   * @returns {string} Markdown prepared for Marked.
   */
  function prepareMarkdown(markdown) {
    var state = {
      fenceCharacter: null,
      fenceLength: 0,
      inlineTicks: 0,
      mathDelimiter: null
    };
    return markdown.split('\n').map(function (line) {
      if (state.fenceCharacter) {
        var closingPattern = new RegExp(
          '^ {0,3}' + state.fenceCharacter + '{' + state.fenceLength + ',}\\s*$'
        );
        if (closingPattern.test(line)) {
          state.fenceCharacter = null;
          state.fenceLength = 0;
        }
        return line;
      }

      if (state.inlineTicks === 0 && state.mathDelimiter === null) {
        var fence = FENCE_PATTERN.exec(line);
        if (fence) {
          state.fenceCharacter = fence[1].charAt(0);
          state.fenceLength = fence[1].length;
          return line;
        }
        if (/^( {4}|\t)/.test(line)) return line;
        if (parseDirective(line)) return '\n' + line.trim() + '\n';
      }

      return protectInlineDollars(line, state);
    }).join('\n');
  }

  /**
   * Protect escaped dollars on one non-fenced Markdown line.
   *
   * @param {string} line Markdown line.
   * @param {{inlineTicks: number, mathDelimiter: string|null}} state Parser state.
   * @returns {string} Prepared line.
   */
  function protectInlineDollars(line, state) {
    var output = '';
    var index = 0;

    while (index < line.length) {
      if (state.mathDelimiter === null && line.charAt(index) === '`') {
        var tickEnd = index;
        while (line.charAt(tickEnd) === '`') tickEnd += 1;
        var tickCount = tickEnd - index;
        if (state.inlineTicks === 0) {
          state.inlineTicks = tickCount;
        } else if (state.inlineTicks === tickCount) {
          state.inlineTicks = 0;
        }
        output += line.slice(index, tickEnd);
        index = tickEnd;
        continue;
      }

      if (state.inlineTicks === 0 && line.charAt(index) === '\\') {
        var slashEnd = index;
        while (line.charAt(slashEnd) === '\\') slashEnd += 1;
        var slashCount = slashEnd - index;
        if (line.charAt(slashEnd) === '$' && slashCount % 2 === 1) {
          if (state.mathDelimiter) {
            output += '\\'.repeat(slashCount + 1) + '$';
          } else {
            output += '\\'.repeat(slashCount - 1) + LITERAL_DOLLAR_HTML;
          }
          index = slashEnd + 1;
          continue;
        }
        output += line.slice(index, slashEnd);
        index = slashEnd;
        continue;
      }

      if (state.inlineTicks === 0 && line.charAt(index) === '$') {
        var delimiter = line.slice(index, index + 2) === '$$' ? '$$' : '$';
        if (state.mathDelimiter === null) {
          state.mathDelimiter = delimiter;
        } else if (state.mathDelimiter === delimiter) {
          state.mathDelimiter = null;
        }
        output += delimiter;
        index += delimiter.length;
        continue;
      }

      output += line.charAt(index);
      index += 1;
    }

    return output;
  }

  /**
   * Parse a supported directive paragraph.
   *
   * @param {string} text Rendered paragraph text.
   * @returns {{name: string, value: string}|null} Parsed directive.
   */
  function parseDirective(text) {
    var match = DIRECTIVE_PATTERN.exec(text.trim());
    if (!match) return null;

    var name = match[1];
    var value = match[2];
    if (name === 'table-border' && (value === 'on' || value === 'off')) {
      return { name: name, value: value };
    }
    if (
      name === 'table-align'
      && TABLE_ALIGNMENT_CLASSES.indexOf('noca-markdown-table-align-' + value) !== -1
    ) {
      return { name: name, value: value };
    }
    if (name === 'align' && ALIGNMENT_CLASSES.indexOf('noca-markdown-align-' + value) !== -1) {
      return { name: name, value: value };
    }
    return null;
  }

  /**
   * Apply one directive when its following block has the required element type.
   *
   * @param {HTMLParagraphElement} marker Directive paragraph.
   * @returns {boolean} Whether the directive was applied.
   */
  function applyMarker(marker) {
    if (!marker.closest(CONTAINER_SELECTOR)) return false;

    var directive = parseDirective(marker.textContent || '');
    if (!directive) return false;

    var target = marker.nextElementSibling;
    while (
      target
      && target.tagName === 'P'
      && parseDirective(target.textContent || '')
    ) {
      target = target.nextElementSibling;
    }

    if (directive.name === 'table-border' || directive.name === 'table-align') {
      if (!target || target.tagName !== 'TABLE') return false;
      if (directive.name === 'table-border') {
        target.classList.toggle('noca-markdown-table-borderless', directive.value === 'off');
      } else {
        TABLE_ALIGNMENT_CLASSES.forEach(function (className) {
          target.classList.remove(className);
        });
        target.classList.add('noca-markdown-table-align-' + directive.value);
      }
    } else {
      if (!target || target.tagName !== 'P') return false;
      ALIGNMENT_CLASSES.forEach(function (className) {
        target.classList.remove(className);
      });
      target.classList.add('noca-markdown-align-' + directive.value);
    }

    marker.remove();
    return true;
  }

  /**
   * Apply directives found at or below a DOM root.
   *
   * @param {Document|Element} root Root containing newly rendered Markdown.
   * @returns {number} Number of directives applied.
   */
  function apply(root) {
    var markers = [];
    if (root.nodeType === Node.ELEMENT_NODE && root.matches('p')) {
      markers.push(root);
    }
    root.querySelectorAll('p').forEach(function (marker) {
      markers.push(marker);
    });

    return markers.reduce(function (count, marker) {
      return count + (applyMarker(marker) ? 1 : 0);
    }, 0);
  }

  /** Begin applying directives to initial and dynamically rendered Markdown. */
  function start() {
    apply(document);
    var observer = new MutationObserver(function (mutations) {
      mutations.forEach(function (mutation) {
        mutation.addedNodes.forEach(function (node) {
          if (node.nodeType === Node.ELEMENT_NODE) apply(node);
        });
      });
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  window.NocaMarkdownDirectives = Object.freeze({
    apply: apply,
    parseDirective: parseDirective,
    prepareMarkdown: prepareMarkdown
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
