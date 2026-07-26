/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/**
 * Populate the problem-set select for the class chosen on problem detail.
 */
document.addEventListener("DOMContentLoaded", function () {
  "use strict";

  const form = document.querySelector("[data-problem-set-assignment-form]");
  const classSelect = document.querySelector("[data-assignment-class-select]");
  const problemSetSelect = document.querySelector("[data-assignment-problem-set-select]");
  const submitButton = document.querySelector("[data-assignment-submit]");
  const optionsScript = document.getElementById("problem-set-assignment-options");

  if (!form || !classSelect || !problemSetSelect || !submitButton || !optionsScript) {
    return;
  }

  let groups = [];
  try {
    const parsed = JSON.parse(optionsScript.textContent || "[]");
    if (Array.isArray(parsed)) {
      groups = parsed;
    }
  } catch {
    groups = [];
  }

  function resetProblemSets(message) {
    problemSetSelect.replaceChildren(new Option(message, ""));
    problemSetSelect.disabled = true;
    submitButton.disabled = true;
  }

  function populateProblemSets() {
    const group = groups.find((item) => item.class_id === classSelect.value);
    if (!group || !Array.isArray(group.problem_sets) || group.problem_sets.length === 0) {
      resetProblemSets(classSelect.value ? "No eligible problem sets" : "Select a class first");
      return;
    }

    problemSetSelect.replaceChildren(new Option("Select a problem set", ""));
    group.problem_sets.forEach((problemSet) => {
      if (typeof problemSet.set_id === "string" && typeof problemSet.name === "string") {
        problemSetSelect.add(new Option(problemSet.name, problemSet.set_id));
      }
    });
    problemSetSelect.disabled = problemSetSelect.options.length <= 1;
    submitButton.disabled = true;
  }

  classSelect.addEventListener("change", populateProblemSets);
  problemSetSelect.addEventListener("change", function () {
    submitButton.disabled = problemSetSelect.disabled || !problemSetSelect.value;
  });
  form.addEventListener("submit", function (event) {
    if (problemSetSelect.disabled || !problemSetSelect.value) {
      event.preventDefault();
    }
  });

  resetProblemSets("Select a class first");
});
