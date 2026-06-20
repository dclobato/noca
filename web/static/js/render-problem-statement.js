// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

document.addEventListener('DOMContentLoaded', async function () {
  var src = document.getElementById('md-statement-src');
  var out = document.getElementById('md-statement-rendered');

  if (!src || !out || typeof marked === 'undefined') {
    return;
  }

  function decodeHtmlEntities(text) {
    var textarea = document.createElement('textarea');
    textarea.innerHTML = text;
    return textarea.value;
  }

  marked.setOptions({
    breaks: true,
    gfm: true
  });

  // O texto da origem está escapado; decodifica antes de parsear markdown
  const rawMarkdown = decodeHtmlEntities(src.textContent || '');
  const rawHtml = marked.parse(rawMarkdown);
  out.innerHTML = DOMPurify.sanitize(rawHtml);

  out.querySelectorAll('pre > code.language-mermaid, pre > code.lang-mermaid').forEach(function (code) {
    var pre = code.parentElement;
    var mermaidBlock = document.createElement('pre');

    var source = code.textContent || '';
    source = source
      .replace(/\r\n/g, '\n')
      .replace(/\r/g, '\n')
      .trim();

    mermaidBlock.className = 'mermaid';
    mermaidBlock.textContent = source;

    console.log('MERMAID INPUT:', JSON.stringify(mermaidBlock.textContent));

    pre.replaceWith(mermaidBlock);
  });

  if (typeof mermaid !== 'undefined') {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict'
    });

    try {
      await mermaid.run({
        nodes: out.querySelectorAll('.mermaid'),
        suppressErrors: false
      });
    } catch (err) {
      console.error('Mermaid render failed:', err);
    }
  }

  if (typeof renderMathInElement === 'function') {
    Array.from(out.children).forEach(function (node) {
      if (node.classList && node.classList.contains('mermaid')) {
        return;
      }

      renderMathInElement(node, {
        delimiters: [
          { left: '$$', right: '$$', display: true },
          { left: '$', right: '$', display: false },
          { left: '\\(', right: '\\)', display: false },
          { left: '\\[', right: '\\]', display: true }
        ],
        throwOnError: false,
        ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code', 'svg']
      });
    });
  }
});

