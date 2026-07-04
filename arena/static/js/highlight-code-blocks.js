/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

document.addEventListener("DOMContentLoaded", function () {
  if (typeof hljs === "undefined") return;

  document.querySelectorAll("pre code").forEach(function (block) {
    hljs.highlightElement(block);
    if (block.hasAttribute("data-highlight-line-numbers") && hljs.lineNumbersBlock) {
      hljs.lineNumbersBlock(block);
    }
  });
});
