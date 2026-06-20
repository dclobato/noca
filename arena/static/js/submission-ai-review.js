/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/**
 * Submission detail AI review renderer.
 *
 * Reads the raw AI response text from the #ai-review-src element, parses it
 * as Markdown using marked.js, sanitizes the result with DOMPurify, and
 * injects the HTML into #ai-review-rendered.
 *
 * Uses the same pipeline as problem-detail.js.
 */

document.addEventListener("DOMContentLoaded", function () {
  "use strict";

  const src = document.getElementById("ai-review-src");
  const out = document.getElementById("ai-review-rendered");

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
});
