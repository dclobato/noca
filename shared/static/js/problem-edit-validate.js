// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Cheap client-side checks before the problem editor submits.
//
// A rejected Save is more expensive than it looks: a browser cannot repopulate an
// `<input type="file">`, so every archive the author attached has to be chosen
// again. Catching the two failures that actually happen -- an empty title and a
// non-positive limit -- keeps the common near-miss from ever reaching the server
// with files attached.
//
// This is a convenience, never a gate: every one of these rules is enforced again
// server-side, and a browser with scripting disabled simply gets the server's
// answer.

(function () {
  "use strict";

  var form = document.getElementById("edit-form");
  if (!form) return;

  function fieldsToCheck() {
    return Array.prototype.slice.call(document.querySelectorAll('[data-editor-required], [data-editor-positive-int]'));
  }

  function problemWith(field) {
    var value = (field.value || "").trim();
    if (field.hasAttribute("data-editor-required") && !value) {
      return "This field is required.";
    }
    if (field.hasAttribute("data-editor-positive-int") && value) {
      if (!/^\d+$/.test(value) || parseInt(value, 10) < 1) {
        return "Enter a whole number of at least 1.";
      }
    }
    return "";
  }

  function showProblem(field, message) {
    field.classList.toggle("is-invalid", !!message);
    field.setAttribute("aria-invalid", message ? "true" : "false");
    field.setCustomValidity(message);

    var describedBy = (field.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
    var feedback = describedBy
      .map(function (id) { return document.getElementById(id); })
      .find(function (element) { return element && element.classList.contains("invalid-feedback"); });
    var feedbackId = feedback ? feedback.id : (field.id ? field.id + "-validation" : "");
    if (!feedback && feedbackId) {
      feedback = document.createElement("div");
      feedback.id = feedbackId;
      feedback.className = "invalid-feedback";
      field.insertAdjacentElement("afterend", feedback);
      if (describedBy.indexOf(feedbackId) === -1) describedBy.push(feedbackId);
      field.setAttribute("aria-describedby", describedBy.join(" "));
    }
    if (feedback) feedback.textContent = message;
  }

  function revealField(field) {
    function focusField() {
      var editor = field.id === "stmt-md-editor"
        ? field.parentElement.querySelector(".EasyMDEContainer .CodeMirror textarea")
        : null;
      (editor || field).focus({ preventScroll: true });
      field.scrollIntoView({ block: "center" });
    }

    var pane = field.closest(".tab-pane");
    if (!pane || pane.classList.contains("active") || !window.bootstrap) {
      focusField();
      return;
    }
    var trigger = document.querySelector('[data-bs-target="#' + pane.id + '"]');
    if (!trigger) {
      focusField();
      return;
    }
    trigger.addEventListener("shown.bs.tab", focusField, { once: true });
    window.bootstrap.Tab.getOrCreateInstance(trigger).show();
  }

  function validateCustomFields() {
    var firstBad = null;
    fieldsToCheck().forEach(function (field) {
      if (field.disabled) return;
      var message = problemWith(field);
      showProblem(field, message);
      if (message && !firstBad) firstBad = field;
    });
    return firstBad;
  }

  form.addEventListener("submit", function (event) {
    var firstBad = validateCustomFields();
    if (!firstBad) return;
    event.preventDefault();
    revealField(firstBad);
  });

  // Native constraint validation happens before `submit`. Capture its non-
  // bubbling `invalid` event at the document so a field associated through
  // `form="edit-form"` can reveal its hidden pane before focus moves to it.
  var firstNativeInvalid = null;
  document.addEventListener("invalid", function (event) {
    var field = event.target;
    if (!field || field.form !== form) return;
    event.preventDefault();
    showProblem(field, field.validationMessage || "Check this field.");
    if (firstNativeInvalid) return;
    firstNativeInvalid = field;
    window.setTimeout(function () {
      var target = firstNativeInvalid;
      firstNativeInvalid = null;
      if (target) revealField(target);
    }, 0);
  }, true);

  document.addEventListener("input", function (event) {
    var field = event.target.closest("input, textarea, select");
    if (!field || field.form !== form) return;
    var message = problemWith(field);
    field.setCustomValidity(message);
    if (field.classList.contains("is-invalid")) showProblem(field, message);
  });

  // Prime native validity without painting untouched fields as invalid. Inline
  // feedback appears only after a Save attempt (or a native invalid event).
  fieldsToCheck().forEach(function (field) {
    field.setCustomValidity(problemWith(field));
  });
})();
