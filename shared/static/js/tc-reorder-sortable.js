//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

//  Shared test-case drag-to-reorder for the Web and Arena admin problem-edit
//  pages. Driven entirely by `data-reorder-*` attributes and the shared
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

  function showError(container, message) {
    const target = targetFor(container) || container.closest(".table-responsive") || container;
    const parent = target.parentElement;
    const existingAlert = parent ? parent.querySelector(".noca-reorder-error") : null;
    if (existingAlert) {
      existingAlert.remove();
    }

    const alert = document.createElement("div");
    alert.className = "alert alert-danger py-2 small mt-2 noca-reorder-error";
    alert.textContent = message;
    target.insertAdjacentElement("afterend", alert);
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

  function initContainer(container) {
    if (container.dataset.reorderInitialized === "true" || !window.Sortable) {
      return;
    }

    const disabled = container.dataset.reorderDisabled === "true";
    let previousOrder = [];

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
        sortable.option("disabled", true);

        postMove(container, event.item, newOrdinal)
          .then(initAll)
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
