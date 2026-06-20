// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

document.addEventListener('DOMContentLoaded', function () {
  var src = document.getElementById('legal-markdown-src');
  var out = document.getElementById('legal-markdown-rendered');

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
  var rawHtml = marked.parse(rawMarkdown);
  out.innerHTML = DOMPurify.sanitize(rawHtml);
});
