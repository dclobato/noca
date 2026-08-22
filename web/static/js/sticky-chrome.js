// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * sticky-chrome.js
 *
 * Publishes the height of the pinned page chrome (navbar + contest navigation
 * band + breadcrumb bar) as the `--noca-chrome-height` custom property on the
 * root element.
 *
 * The chrome sticks on its own, with no help from JavaScript -- `_base.html`
 * wraps the three bars in one `position: sticky` element, so their offsets can
 * never disagree. What CSS cannot express is how much of the viewport that
 * element covers, which anything else that wants to sit below it needs: anchor
 * targets (`scroll-padding-top`) and the problem editor's own sticky save bar.
 * That height depends on the viewport width, on whether a contest is in scope
 * and on whether the page has a breadcrumb, so it is measured rather than
 * assumed.
 *
 * `_base.html` loads this file synchronously after the chrome and before
 * `<main>`. The browser therefore receives the measured scroll padding before
 * it parses a fragment target in the page body and performs the initial jump.
 *
 * The property reports 0 whenever the chrome is not actually pinned -- the
 * short-viewport media query drops it back into the flow -- so consumers never
 * reserve space for chrome that scrolls away.
 */
(function () {
  "use strict";

  const chrome = document.querySelector(".noca-sticky-chrome");
  if (!chrome) return;

  let published = null;

  function publish() {
    const pinned = getComputedStyle(chrome).position === "sticky";
    const height = pinned ? Math.round(chrome.getBoundingClientRect().height) : 0;
    if (height === published) return;
    published = height;
    document.documentElement.style.setProperty(
      "--noca-chrome-height",
      height + "px",
    );
  }

  publish();

  // The band's labels can wrap and the clock can grow a phase, so the chrome
  // changes height without the window changing size.
  if (typeof ResizeObserver === "function") {
    new ResizeObserver(publish).observe(chrome);
  }
  // A resize can flip the short-viewport media query without changing the
  // chrome's own box, which the observer would not report.
  window.addEventListener("resize", publish, { passive: true });
})();
