// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

document.addEventListener('DOMContentLoaded', function () {
  var src = document.getElementById('markdown-src');
  var out = document.getElementById('markdown-rendered');

  if (!src || !out || typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
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

  var rawMarkdown = decodeHtmlEntities(src.textContent || '');
  var preparedMarkdown = window.NocaMarkdownDirectives
    ? window.NocaMarkdownDirectives.prepareMarkdown(rawMarkdown)
    : rawMarkdown;
  var rawHtml = marked.parse(preparedMarkdown);
  out.innerHTML = DOMPurify.sanitize(rawHtml);
});
