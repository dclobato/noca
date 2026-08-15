/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 The NOCA Authors (see AUTHORS)
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/**
 * Help surface: mark the section index entry for whatever the reader is
 * looking at.
 *
 * Every section stays in the document, so this only reflects position; it
 * never shows or hides content. With no IntersectionObserver the links remain
 * ordinary anchors, which is the whole behaviour minus the highlight.
 */

document.addEventListener("DOMContentLoaded", () => {
  "use strict";

  const index = document.querySelector("[data-help-index]");
  if (!index || typeof IntersectionObserver === "undefined") {
    return;
  }

  const links = new Map();
  index.querySelectorAll("a[href^='#']").forEach((link) => {
    const id = decodeURIComponent(link.getAttribute("href").slice(1));
    const section = id ? document.getElementById(id) : null;
    if (section) {
      links.set(section, link);
    }
  });

  if (links.size === 0) {
    return;
  }

  const visible = new Set();

  /** Mark the topmost visible section, falling back to the last one passed. */
  function refresh() {
    let current = null;
    links.forEach((_link, section) => {
      if (!visible.has(section)) {
        return;
      }
      if (current === null || section.offsetTop < current.offsetTop) {
        current = section;
      }
    });

    links.forEach((link, section) => {
      if (section === current) {
        link.setAttribute("aria-current", "true");
      } else {
        link.removeAttribute("aria-current");
      }
    });
  }

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          visible.add(entry.target);
        } else {
          visible.delete(entry.target);
        }
      });
      refresh();
    },
    { rootMargin: "-88px 0px -55% 0px", threshold: 0 },
  );

  links.forEach((_link, section) => observer.observe(section));
});
