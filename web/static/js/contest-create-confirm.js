// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

(function () {
  const form = document.getElementById("contest-create-form");
  const openBtn = document.getElementById("open-contest-confirm-modal");
  const modal = document.getElementById("contest-create-confirm-modal");
  if (!form || !openBtn || !modal) return;

  const bsModal = new bootstrap.Modal(modal);
  const languageList = document.getElementById("confirm-language-list");
  const noLanguagesMsg = document.getElementById("confirm-no-languages");
  const confirmBtn = document.getElementById("confirm-create-contest-btn");
  const publicationWarning = document.getElementById("confirm-publication-warning");

  function fieldValue(name, fallback) {
    const field = form.elements.namedItem(name);
    if (!field || !field.value.trim()) return fallback;
    return field.value.trim();
  }

  function radioValue(name) {
    const field = form.querySelector(`input[name="${name}"]:checked`);
    return field && field.value === "yes" ? "Enabled" : "Disabled";
  }

  function setReviewText(id, value) {
    document.getElementById(id).textContent = value;
  }

  function minutes(name) {
    const value = fieldValue(name, "0");
    return `${value} ${value === "1" ? "minute" : "minutes"}`;
  }

  function populateReview() {
    const timezone = form.elements.namedItem("contest_timezone");
    const timezoneLabel = timezone && timezone.selectedOptions.length
      ? timezone.selectedOptions[0].textContent.trim()
      : "Not selected";
    const password = fieldValue("owner_password", "");
    const publication = form.querySelector(
      'input[name="release_problem_set_after_end"]:checked'
    );

    setReviewText("confirm-contest-name", fieldValue("contest_name", "Not provided"));
    setReviewText("confirm-login-slug", fieldValue("login_slug", "Not provided"));
    setReviewText("confirm-contest-url", fieldValue("contest_url", "Not provided"));
    setReviewText("confirm-start-time", fieldValue("start_time", "Not provided"));
    setReviewText("confirm-timezone", timezoneLabel);
    setReviewText("confirm-duration", minutes("duration_minutes"));
    setReviewText("confirm-scoreboard-stop", minutes("stop_updating_scoreboard"));
    setReviewText("confirm-answers-stop", minutes("stop_answers_after"));
    setReviewText("confirm-clarification-timeout", minutes("clarifications_timeout_minutes"));
    setReviewText("confirm-task-timeout", minutes("tasks_timeout_minutes"));
    setReviewText("confirm-review-timeout", minutes("review_timeout_minutes"));
    setReviewText("confirm-wa-penalty", minutes("wa_penalty"));
    setReviewText("confirm-file-size", `${fieldValue("max_problem_file_size_bytes", "0")} bytes`);
    setReviewText("confirm-show-limits", radioValue("show_limits"));
    setReviewText("confirm-autojudge-only", radioValue("autojudge_only"));
    setReviewText("confirm-print-requests", radioValue("allow_print_requests"));
    setReviewText("confirm-accept-pe", radioValue("accept_pe"));
    setReviewText("confirm-ce-penalty", radioValue("ce_adds_penalty"));
    setReviewText("confirm-owner-fullname", fieldValue("owner_fullname", "Not provided"));
    setReviewText("confirm-owner-username", fieldValue("owner_username", "Not provided"));
    setReviewText("confirm-owner-email", fieldValue("owner_email", "Not provided"));
    setReviewText(
      "confirm-owner-password",
      password ? "Provided by you" : "Secure password will be generated"
    );
    setReviewText(
      "confirm-publication",
      publication && publication.value === "yes"
        ? "Publish complete problem set after the contest ends"
        : "Keep complete problem set private"
    );
  }

  openBtn.addEventListener("click", function () {
    if (
      window.NocaContestWizard &&
      !window.NocaContestWizard.validateAll()
    ) {
      return;
    }
    populateReview();
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
    const publishesProblemSet = form.querySelector(
      'input[name="release_problem_set_after_end"]:checked'
    );
    publicationWarning.classList.toggle(
      "d-none",
      !publishesProblemSet || publishesProblemSet.value !== "yes"
    );
    bsModal.show();
  });

  confirmBtn.addEventListener("click", function () {
    confirmBtn.disabled = true;
    confirmBtn.textContent = "Creating contest…";
    bsModal.hide();
    form.submit();
  });
})();
