// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Renders sample test-case explanations as Markdown (line breaks preserved)
// with inline/display KaTeX math. Each target element carries the raw
// explanation text as its initial textContent and is matched by the
// `data-tc-explanation` attribute, so multiple explanations render on one page.

document.addEventListener('DOMContentLoaded', function () {
  'use strict';

  var nodes = document.querySelectorAll('[data-tc-explanation]');

  if (!nodes.length || typeof marked === 'undefined') {
    return;
  }

  marked.setOptions({ breaks: true, gfm: true });

  nodes.forEach(function (el) {
    // textContent already holds the decoded explanation (newlines preserved).
    var rawMarkdown = el.textContent || '';
    var rawHtml = marked.parse(rawMarkdown);
    el.innerHTML = typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(rawHtml) : rawHtml;

    if (typeof renderMathInElement === 'function') {
      renderMathInElement(el, {
        delimiters: [
          { left: '$$', right: '$$', display: true },
          { left: '$', right: '$', display: false },
          { left: '\\(', right: '\\)', display: false },
          { left: '\\[', right: '\\]', display: true }
        ],
        throwOnError: false,
        ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code', 'svg']
      });
    }
  });
});
