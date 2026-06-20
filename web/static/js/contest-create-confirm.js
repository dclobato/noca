// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

(function () {
  const form = document.querySelector('form[action*="/contests/new"]');
  const openBtn = document.getElementById("open-contest-confirm-modal");
  const modal = document.getElementById("contest-create-confirm-modal");
  if (!form || !openBtn || !modal) return;

  const bsModal = new bootstrap.Modal(modal);
  const languageList = document.getElementById("confirm-language-list");
  const noLanguagesMsg = document.getElementById("confirm-no-languages");
  const confirmBtn = document.getElementById("confirm-create-contest-btn");

  openBtn.addEventListener("click", function () {
    const checked = Array.from(form.querySelectorAll('input[name="language_ids"]:checked'));
    languageList.innerHTML = "";
    if (checked.length === 0) {
      languageList.classList.add("d-none");
      noLanguagesMsg.classList.remove("d-none");
      confirmBtn.disabled = true;
    } else {
      noLanguagesMsg.classList.add("d-none");
      languageList.classList.remove("d-none");
      confirmBtn.disabled = false;
      checked.forEach(function (cb) {
        const li = document.createElement("li");
        const icon = document.createElement("i");
        icon.className = (cb.dataset.langIcon || "") + " me-1";
        icon.setAttribute("aria-hidden", "true");
        li.appendChild(icon);
        li.appendChild(document.createTextNode(cb.dataset.langName || cb.value));
        languageList.appendChild(li);
      });
    }
    bsModal.show();
  });

  confirmBtn.addEventListener("click", function () {
    bsModal.hide();
    form.submit();
  });
})();
