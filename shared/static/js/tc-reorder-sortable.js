//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

//  Shared drag-to-reorder for the Web and Arena judgment-data pages and the Web
//  problem list. Driven entirely by `data-reorder-*` attributes and the shared
//  `.noca-drag-handle` / `.noca-sortable-*` classes, so both modules reuse it
//  without change. The move endpoint returns the refreshed list partial, which
//  replaces the element named by `data-reorder-target`.

(function () {
  "use strict";

  function moveUrl(row, newOrdinal) {
    const url = new URL(row.dataset.moveUrl, window.location.origin);
    url.searchParams.set("new_ordinal", String(newOrdinal));
    return url.toString();
  }

  function targetFor(container) {
    const targetId = container.dataset.reorderTarget;
    if (!targetId) {
      return null;
    }
    return document.getElementById(targetId);
  }

  function announce(message) {
    var region = document.getElementById("noca-reorder-status");
    if (!region) {
      region = document.createElement("div");
      region.id = "noca-reorder-status";
      region.className = "visually-hidden";
      region.setAttribute("role", "status");
      region.setAttribute("aria-live", "polite");
      region.setAttribute("aria-atomic", "true");
      document.body.appendChild(region);
    }
    region.textContent = "";
    window.setTimeout(function () {
      region.textContent = message;
    }, 0);
  }

  function showError(container, message) {
    const target = targetFor(container) || container.closest(".table-responsive") || container;
    const parent = target.parentElement;
    const existingAlert = parent ? parent.querySelector(".noca-reorder-error") : null;
    if (existingAlert) {
      existingAlert.remove();
    }

    const alert = document.createElement("div");
    alert.className = "alert alert-danger py-2 small mt-2 noca-reorder-error";
    alert.setAttribute("role", "alert");
    alert.textContent = message;
    target.insertAdjacentElement("afterend", alert);
    announce(message);
  }

  async function postMove(container, row, newOrdinal) {
    const response = await fetch(moveUrl(row, newOrdinal), {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "HX-Request": "true"
      }
    });
    if (!response.ok) {
      throw new Error("Could not save the new order.");
    }

    const html = await response.text();
    const target = targetFor(container);
    if (target) {
      target.outerHTML = html;
    }
  }

  function focusMovedRow(rowId) {
    const row = Array.prototype.find.call(
      document.querySelectorAll("tr[data-id]"),
      function (candidate) { return candidate.dataset.id === rowId; }
    );
    const handle = row ? row.querySelector(".noca-drag-handle") : null;
    if (handle) handle.focus();
  }

  function keyboardMove(container, event, disabled) {
    const handle = event.target.closest(".noca-drag-handle");
    if (!handle || disabled || handle.getAttribute("aria-disabled") === "true") {
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      announce("Use the Up and Down arrow keys to move " + handle.dataset.reorderLabel + ".");
      return;
    }
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    const row = handle.closest("tr[data-id]");
    const rows = Array.from(container.querySelectorAll("tr[data-id]"));
    const oldIndex = rows.indexOf(row);
    const newIndex = oldIndex + (event.key === "ArrowUp" ? -1 : 1);
    event.preventDefault();
    if (oldIndex < 0 || newIndex < 0 || newIndex >= rows.length) {
      announce(handle.dataset.reorderLabel + " is already at the edge of the list.");
      return;
    }

    const rowId = row.dataset.id;
    const label = handle.dataset.reorderLabel || "Item";
    handle.setAttribute("aria-disabled", "true");
    postMove(container, row, newIndex + 1)
      .then(function () {
        initAll();
        announce(label + " moved to position " + (newIndex + 1) + ".");
        focusMovedRow(rowId);
      })
      .catch(function () {
        handle.setAttribute("aria-disabled", "false");
        showError(container, "Order was not saved. The previous order has been restored.");
        handle.focus();
      });
  }

  function initContainer(container) {
    if (container.dataset.reorderInitialized === "true" || !window.Sortable) {
      return;
    }

    const disabled = container.dataset.reorderDisabled === "true";
    let previousOrder = [];
    container.addEventListener("keydown", function (event) {
      keyboardMove(container, event, disabled);
    });

    const sortable = window.Sortable.create(container, {
      animation: 150,
      disabled: disabled,
      draggable: "tr[data-id]",
      handle: ".noca-drag-handle",
      dataIdAttr: "data-id",
      ghostClass: "noca-sortable-ghost",
      chosenClass: "noca-sortable-chosen",
      filter: "a, button, input, textarea, select, label, .tc-preview",
      preventOnFilter: false,
      onStart: function () {
        previousOrder = sortable.toArray();
        container.classList.add("noca-sortable-active");
      },
      onEnd: function (event) {
        container.classList.remove("noca-sortable-active");
        if (event.oldDraggableIndex === event.newDraggableIndex) {
          return;
        }

        const newOrdinal = event.newDraggableIndex + 1;
        const label = event.item.querySelector(".noca-drag-handle").dataset.reorderLabel || "Item";
        sortable.option("disabled", true);

        postMove(container, event.item, newOrdinal)
          .then(function () {
            initAll();
            announce(label + " moved to position " + newOrdinal + ".");
          })
          .catch(function () {
            sortable.sort(previousOrder, true);
            sortable.option("disabled", disabled);
            showError(container, "Order was not saved. The previous order has been restored.");
          });
      }
    });

    container.dataset.reorderInitialized = "true";
  }

  function initAll() {
    document.querySelectorAll("[data-reorder-sortable='true']").forEach(initContainer);
  }

  document.addEventListener("DOMContentLoaded", initAll);
  document.body.addEventListener("htmx:afterSwap", initAll);
})();
