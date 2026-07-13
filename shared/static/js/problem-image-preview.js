/*
 *  NOCA -- Next Online Contest Administrator
 *  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 *
 *  Client-side preview for the shared problem-image field. Self-initializing on
 *  any file input carrying data-image-preview, so both the Arena and Contest
 *  admin problem forms get the preview by including the shared partial.
 */
(() => {
  "use strict";

  function attach(input) {
    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      if (!file) return;

      const existing = document.getElementById("image-preview");
      if (existing) existing.remove();

      const reader = new FileReader();
      reader.onload = (e) => {
        const img = document.createElement("img");
        img.id = "image-preview";
        img.src = e.target.result;
        img.alt = "Image preview";
        img.className = "img-thumbnail mt-2 noca-problem-image-preview";
        input.insertAdjacentElement("afterend", img);
      };
      reader.readAsDataURL(file);
    });
  }

  function init() {
    document.querySelectorAll("input[type=file][data-image-preview]").forEach(attach);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
