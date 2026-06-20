// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

(function () {
  const form = document.getElementById("runs-submit-form");
  const modal = document.getElementById("runs-confirm-modal");
  if (!form || !modal) return;

  const limitsData = JSON.parse(document.getElementById("runs-limits-data").textContent);
  const langMap = JSON.parse(document.getElementById("runs-lang-map").textContent);
  const langIconMap = JSON.parse(document.getElementById("runs-lang-icon-map").textContent);
  const problemLabels = JSON.parse(document.getElementById("runs-problem-labels").textContent);

  const bsModal = new bootstrap.Modal(modal);

  const elProblem = document.getElementById("confirm-problem");
  const elLang = document.getElementById("confirm-lang");
  const elTime = document.getElementById("confirm-time");
  const elMemory = document.getElementById("confirm-memory");
  const elPids = document.getElementById("confirm-pids");
  const elOutput = document.getElementById("confirm-output");
  const btnConfirm = document.getElementById("confirm-submit-btn");

  form.addEventListener("submit", function (e) {
    const problemId = form.problem_id.value;
    const langId = form.language_id.value;

    if (!problemId || !langId) return; // let native validation handle it

    e.preventDefault();

    const problemEntry = limitsData[problemId] || {};
    const limits = problemEntry[langId] || problemEntry["default"] || {};

    elProblem.textContent = problemLabels[problemId] || problemId;
    const langName = langMap[langId] || langId;
    const langIcon = langIconMap[langId] || "";
    elLang.innerHTML = langIcon
      ? `<i class="${langIcon} me-1"></i>${langName}`
      : langName;
    elTime.textContent = limits.time_ms != null ? limits.time_ms + " ms" : "—";
    elMemory.textContent = limits.memory_kb != null ? limits.memory_kb + " KB" : "—";
    elPids.textContent = limits.pids != null ? limits.pids : "—";
    elOutput.textContent = limits.output_bytes != null ? limits.output_bytes + " bytes" : "No limit";

    bsModal.show();
  });

  btnConfirm.addEventListener("click", function () {
    bsModal.hide();
    form.submit(); // native submit() does not fire the submit event, so no loop
  });
})();
