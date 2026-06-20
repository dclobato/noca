/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/**
 * Vertical resize handle for the Ace code editor on the problem detail page.
 *
 * Dragging the handle below the editor changes its explicit pixel height.
 * The chosen height is persisted in localStorage and restored on future visits.
 * Double-clicking the handle resets to the default height.
 * Keyboard: ArrowUp/ArrowDown (±10 px, ±50 px with Shift).
 * Dispatches arena:problem-layout-resize so the Ace instance reflows itself.
 */

document.addEventListener("DOMContentLoaded", function () {
  "use strict";

  const resizerEl = document.querySelector("[data-editor-resizer]");
  const editorEl = document.getElementById("code-editor");

  if (!resizerEl || !editorEl) {
    return;
  }

  const storageKey = "noca-arena-editor-height";
  const defaultHeight = 480;
  const minimumHeight = 120;
  const maximumHeight = 2000;

  const notifyLayoutChange = function () {
    document.dispatchEvent(new CustomEvent("arena:problem-layout-resize"));
  };

  const applyHeight = function (value, persist) {
    const height = Math.min(maximumHeight, Math.max(minimumHeight, Math.round(value)));
    editorEl.style.height = height + "px";
    if (persist) {
      try {
        window.localStorage.setItem(storageKey, String(height));
      } catch (_error) {
        // Storage unavailable in privacy-restricted contexts.
      }
    }
    notifyLayoutChange();
  };

  const storedHeight = function () {
    try {
      const value = Number.parseInt(window.localStorage.getItem(storageKey), 10);
      return Number.isFinite(value) ? value : defaultHeight;
    } catch (_error) {
      return defaultHeight;
    }
  };

  applyHeight(storedHeight(), false);

  let startY = 0;
  let startHeight = 0;

  resizerEl.addEventListener("pointerdown", function (event) {
    if (event.button !== 0) {
      return;
    }
    event.preventDefault();
    resizerEl.setPointerCapture(event.pointerId);
    resizerEl.classList.add("is-dragging");
    document.body.classList.add("arena-editor-resizing");
    startY = event.clientY;
    startHeight = editorEl.getBoundingClientRect().height;
  });

  resizerEl.addEventListener("pointermove", function (event) {
    if (!resizerEl.hasPointerCapture(event.pointerId)) {
      return;
    }
    applyHeight(startHeight + (event.clientY - startY), false);
  });

  const finishDragging = function (event, persist) {
    if (!resizerEl.hasPointerCapture(event.pointerId)) {
      return;
    }
    if (persist) {
      const currentHeight =
        Number.parseInt(editorEl.style.height, 10) || defaultHeight;
      applyHeight(currentHeight, true);
    }
    resizerEl.releasePointerCapture(event.pointerId);
    resizerEl.classList.remove("is-dragging");
    document.body.classList.remove("arena-editor-resizing");
  };

  resizerEl.addEventListener("pointerup", function (event) {
    finishDragging(event, true);
  });
  resizerEl.addEventListener("pointercancel", function (event) {
    finishDragging(event, false);
  });

  resizerEl.addEventListener("dblclick", function () {
    applyHeight(defaultHeight, true);
  });

  resizerEl.addEventListener("keydown", function (event) {
    const currentHeight =
      Number.parseInt(editorEl.style.height, 10) || defaultHeight;
    const step = event.shiftKey ? 50 : 10;
    let nextHeight = null;

    if (event.key === "ArrowDown") {
      nextHeight = currentHeight + step;
    } else if (event.key === "ArrowUp") {
      nextHeight = currentHeight - step;
    } else if (event.key === "Home") {
      nextHeight = minimumHeight;
    } else if (event.key === "End") {
      nextHeight = maximumHeight;
    }

    if (nextHeight === null) {
      return;
    }
    event.preventDefault();
    applyHeight(nextHeight, true);
  });
});
