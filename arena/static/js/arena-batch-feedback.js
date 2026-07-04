// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

function changedTextareas() {
  return Array.from(document.querySelectorAll("[data-batch-feedback-textarea]")).filter((textarea) => {
    const value = textarea.value.trim();
    return value !== "" && value !== textarea.defaultValue.trim();
  });
}

function setupBatchFeedbackConfirm() {
  const form = document.querySelector("[data-batch-feedback-form]");
  const modalEl = document.getElementById("batch-feedback-confirm-modal");
  const submitButton = document.querySelector("[data-batch-feedback-submit]");
  const confirmButton = document.querySelector("[data-batch-feedback-confirm-button]");
  const confirmText = document.querySelector("[data-batch-feedback-confirm-text]");
  if (!form || !modalEl || !submitButton || !confirmButton || !confirmText || typeof bootstrap === "undefined") return;

  const bsModal = new bootstrap.Modal(modalEl);
  const arenaNumber = form.dataset.arenaNumber || "";
  const problemTitle = form.dataset.problemTitle || "";

  submitButton.addEventListener("click", () => {
    const changed = changedTextareas();
    if (!changed.length) return;
    const plural = changed.length !== 1 ? "s" : "";
    confirmText.textContent =
      `Confirm ${changed.length} feedback${plural} for students on problem ${arenaNumber} - ${problemTitle}?`;
    bsModal.show();
  });

  confirmButton.addEventListener("click", () => {
    const changedNames = new Set(changedTextareas().map((textarea) => textarea.name));
    form.querySelectorAll("[data-batch-feedback-textarea]").forEach((textarea) => {
      if (!changedNames.has(textarea.name)) textarea.removeAttribute("name");
    });
    bsModal.hide();
    form.submit();
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setupBatchFeedbackConfirm();
});
