/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/**
 * Resizable desktop workspace for the problem statement and solution editor.
 *
 * Pointer dragging and keyboard controls update the statement-panel percentage.
 * The selected ratio is persisted for future problem pages. On narrow screens,
 * CSS stacks the panels and hides the separator.
 */

document.addEventListener("DOMContentLoaded", function () {
  "use strict";

  const workspace = document.querySelector("[data-problem-workspace]");
  const resizer = document.querySelector("[data-problem-workspace-resizer]");

  if (!workspace || !resizer) {
    return;
  }

  const storageKey = "noca-arena-problem-statement-width";
  const widthProperty = "--arena-problem-statement-width";
  const defaultWidth = 58.333;
  const minimumWidth = 25;
  const maximumWidth = 75;
  const desktopMedia = window.matchMedia("(min-width: 768px)");
  let animationFrame = null;

  const clamp = function (value) {
    return Math.min(maximumWidth, Math.max(minimumWidth, value));
  };

  const notifyLayoutChange = function () {
    if (animationFrame !== null) {
      return;
    }
    animationFrame = window.requestAnimationFrame(function () {
      animationFrame = null;
      document.dispatchEvent(new CustomEvent("arena:problem-layout-resize"));
    });
  };

  const applyWidth = function (value, persist) {
    const width = clamp(value);
    workspace.style.setProperty(widthProperty, width + "%");
    resizer.setAttribute("aria-valuenow", String(Math.round(width)));
    resizer.setAttribute("aria-valuetext", Math.round(width) + "% problem statement");

    if (persist) {
      try {
        window.localStorage.setItem(storageKey, String(width));
      } catch (_error) {
        // Storage can be unavailable in privacy-restricted browser contexts.
      }
    }
    notifyLayoutChange();
  };

  const storedWidth = function () {
    try {
      const value = Number.parseFloat(window.localStorage.getItem(storageKey));
      return Number.isFinite(value) ? value : defaultWidth;
    } catch (_error) {
      return defaultWidth;
    }
  };

  const widthFromPointer = function (clientX) {
    const bounds = workspace.getBoundingClientRect();
    return ((clientX - bounds.left) / bounds.width) * 100;
  };

  applyWidth(storedWidth(), false);

  resizer.addEventListener("pointerdown", function (event) {
    if (!desktopMedia.matches || event.button !== 0) {
      return;
    }

    event.preventDefault();
    resizer.setPointerCapture(event.pointerId);
    resizer.classList.add("is-dragging");
    document.body.classList.add("arena-problem-workspace-dragging");
    applyWidth(widthFromPointer(event.clientX), false);
  });

  resizer.addEventListener("pointermove", function (event) {
    if (!resizer.hasPointerCapture(event.pointerId)) {
      return;
    }
    applyWidth(widthFromPointer(event.clientX), false);
  });

  const finishDragging = function (event, persistPointerPosition) {
    if (!resizer.hasPointerCapture(event.pointerId)) {
      return;
    }
    if (persistPointerPosition) {
      applyWidth(widthFromPointer(event.clientX), true);
    } else {
      const currentWidth = Number.parseFloat(
        workspace.style.getPropertyValue(widthProperty),
      );
      applyWidth(currentWidth || defaultWidth, true);
    }
    resizer.releasePointerCapture(event.pointerId);
    resizer.classList.remove("is-dragging");
    document.body.classList.remove("arena-problem-workspace-dragging");
  };

  resizer.addEventListener("pointerup", function (event) {
    finishDragging(event, true);
  });
  resizer.addEventListener("pointercancel", function (event) {
    finishDragging(event, false);
  });

  resizer.addEventListener("dblclick", function () {
    applyWidth(defaultWidth, true);
  });

  resizer.addEventListener("keydown", function (event) {
    const currentWidth = Number.parseFloat(
      workspace.style.getPropertyValue(widthProperty),
    ) || defaultWidth;
    const step = event.shiftKey ? 10 : 2;
    let nextWidth = null;

    if (event.key === "ArrowLeft") {
      nextWidth = currentWidth - step;
    } else if (event.key === "ArrowRight") {
      nextWidth = currentWidth + step;
    } else if (event.key === "Home") {
      nextWidth = minimumWidth;
    } else if (event.key === "End") {
      nextWidth = maximumWidth;
    }

    if (nextWidth === null) {
      return;
    }
    event.preventDefault();
    applyWidth(nextWidth, true);
  });
});
