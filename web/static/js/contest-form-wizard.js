// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

(function () {
  const form = document.querySelector("[data-contest-wizard]");
  if (!form) return;

  const panels = Array.from(form.querySelectorAll("[data-wizard-panel]"));
  const indicators = Array.from(
    form.querySelectorAll("[data-wizard-step-indicator]")
  );
  const progress = form.querySelector("[data-wizard-progress]");
  const progressbar = progress ? progress.closest("[role='progressbar']") : null;
  let currentStep = 0;
  let validatingAll = false;

  function showStep(stepIndex, focusHeading) {
    currentStep = Math.max(0, Math.min(stepIndex, panels.length - 1));
    panels.forEach(function (panel, index) {
      panel.classList.toggle("is-active", index === currentStep);
    });
    indicators.forEach(function (indicator, index) {
      const active = index === currentStep;
      indicator.classList.toggle("is-active", active);
      if (active) indicator.setAttribute("aria-current", "step");
      else indicator.removeAttribute("aria-current");
    });
    if (progress) {
      progress.dataset.step = String(currentStep + 1);
    }
    if (progressbar) {
      progressbar.setAttribute("aria-valuenow", String(currentStep + 1));
    }
    if (focusHeading) {
      const heading = panels[currentStep].querySelector("h2");
      if (heading) {
        heading.tabIndex = -1;
        heading.focus();
      }
    }
  }

  function firstInvalidControl(panel) {
    return Array.from(panel.querySelectorAll("input, select, textarea")).find(
      function (control) {
        return !control.disabled && !control.checkValidity();
      }
    );
  }

  function validatePanel(panel) {
    const invalidControl = firstInvalidControl(panel);
    if (!invalidControl) return true;
    invalidControl.reportValidity();
    return false;
  }

  function validateAll() {
    validatingAll = true;
    const invalidPanelIndex = panels.findIndex(function (panel) {
      return !!firstInvalidControl(panel);
    });
    validatingAll = false;
    if (invalidPanelIndex === -1) return true;

    showStep(invalidPanelIndex, false);
    const invalidControl = firstInvalidControl(panels[invalidPanelIndex]);
    if (invalidControl) invalidControl.reportValidity();
    return false;
  }

  function setTimingDefaultsFromDuration() {
    const durationInput = form.querySelector("#duration_minutes");
    const scoreboardInput = form.querySelector("#stop_updating_scoreboard");
    const answersInput = form.querySelector("#stop_answers_after");
    if (!durationInput || !scoreboardInput || !answersInput) return;

    const durationMinutes = Number.parseInt(durationInput.value, 10);
    if (!Number.isFinite(durationMinutes)) return;

    scoreboardInput.value = String(Math.max(1, durationMinutes - 20));
    answersInput.value = String(Math.max(1, durationMinutes - 10));
    scoreboardInput.dispatchEvent(new Event("input", { bubbles: true }));
    answersInput.dispatchEvent(new Event("input", { bubbles: true }));
  }

  form.addEventListener("click", function (event) {
    const nextButton = event.target.closest("[data-wizard-next]");
    if (nextButton) {
      if (validatePanel(panels[currentStep])) {
        if (currentStep === 0) setTimingDefaultsFromDuration();
        showStep(currentStep + 1, true);
      }
      return;
    }

    if (event.target.closest("[data-wizard-back]")) {
      showStep(currentStep - 1, true);
    }
  });

  form.addEventListener(
    "invalid",
    function (event) {
      if (validatingAll) return;
      const panelIndex = panels.findIndex(function (panel) {
        return panel.contains(event.target);
      });
      if (panelIndex >= 0) showStep(panelIndex, false);
    },
    true
  );

  form.classList.add("is-ready");
  showStep(0, false);
  window.NocaContestWizard = { validateAll: validateAll };
})();
