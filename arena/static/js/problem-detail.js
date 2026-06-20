/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/**
 * Public problem detail page: renders the Markdown problem statement.
 *
 * Pipeline (all libraries loaded from CDN via defer):
 *   1. marked.parse()       — Markdown → HTML
 *   2. DOMPurify.sanitize() — strip unsafe HTML
 *   3. DOM injection        — insert into #md-statement-rendered
 *   4. Mermaid              — replace language-mermaid code blocks
 *   5. renderMathInElement  — KaTeX inline/display math
 */

document.addEventListener("DOMContentLoaded", async function () {
  "use strict";

  const src = document.getElementById("md-statement-src");
  const out = document.getElementById("md-statement-rendered");

  if (!src || !out || typeof marked === "undefined") {
    return;
  }

  function decodeHtmlEntities(text) {
    const ta = document.createElement("textarea");
    ta.innerHTML = text;
    return ta.value;
  }

  marked.setOptions({ breaks: true, gfm: true });

  const rawMarkdown = decodeHtmlEntities(src.textContent || "");
  const rawHtml     = marked.parse(rawMarkdown);
  out.innerHTML     = typeof DOMPurify !== "undefined"
    ? DOMPurify.sanitize(rawHtml)
    : rawHtml;

  // Replace language-mermaid fenced code blocks with .mermaid <pre> elements
  out.querySelectorAll("pre > code.language-mermaid, pre > code.lang-mermaid").forEach((code) => {
    const pre = code.parentElement;
    const mermaidBlock = document.createElement("pre");
    let source = (code.textContent || "")
      .replace(/\r\n/g, "\n")
      .replace(/\r/g, "\n")
      .trim();
    mermaidBlock.className    = "mermaid";
    mermaidBlock.textContent  = source;
    pre.replaceWith(mermaidBlock);
  });

  if (typeof mermaid !== "undefined") {
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
    try {
      await mermaid.run({
        nodes: out.querySelectorAll(".mermaid"),
        suppressErrors: false,
      });
    } catch (err) {
      console.error("Mermaid render failed:", err);
    }
  }

  if (typeof renderMathInElement === "function") {
    Array.from(out.children).forEach(function (node) {
      if (node.classList && node.classList.contains("mermaid")) return;
      renderMathInElement(node, {
        delimiters: [
          { left: "$$", right: "$$", display: true },
          { left: "$",  right: "$",  display: false },
          { left: "\\(", right: "\\)", display: false },
          { left: "\\[", right: "\\]", display: true },
        ],
        throwOnError: false,
        ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code", "svg"],
      });
    });
  }
});
