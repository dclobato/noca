// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// The single home of the `data-submit-once` courtesy: a form carrying the
// attribute is submitted at most once per page view. Its submit controls are
// disabled on the way out, and an element marked `data-submit-once-label`
// inside one of them takes the busy wording the attribute carries (a verb
// phrase, e.g. "Unlocking…"), so an impatient second click on a slow link
// cannot post the same privileged action twice.
//
// The listener is delegated on the document, so a form swapped in later keeps
// it with no per-page wiring. Both modules load this script from their
// `_base.html`, and no page may load a second copy or carry its own listener.
//
// Three deliberate details:
//   * The disable is deferred by one event-loop turn, because a submit control
//     disabled during its own submit event is not sent with the form.
//   * `submit` fires only once the browser's own validation has passed, so a
//     form refused for an empty required field is never marked busy.
//   * `pageshow` restores every control, because the back button serves this
//     page from the bfcache with the DOM exactly as it was left -- disabled.

(() => {
  "use strict";

  const IDLE = "submitOnceIdle";

  const submitControlsOf = (form) => {
    const owned = form.id
      ? document.querySelectorAll(`[form="${CSS.escape(form.id)}"]`)
      : [];
    return Array.from(form.elements)
      .concat(Array.from(owned))
      .filter((element) => element.type === "submit");
  };

  const labelOf = (control) =>
    control.querySelector ? control.querySelector("[data-submit-once-label]") : null;

  document.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-submit-once]");
    if (!form || event.defaultPrevented) return;
    if (form.dataset.submitOnceBusy === "true") {
      event.preventDefault();
      return;
    }
    form.dataset.submitOnceBusy = "true";
    const busy = form.dataset.submitOnce;
    const controls = submitControlsOf(form);
    window.setTimeout(() => {
      form.setAttribute("aria-busy", "true");
      for (const control of controls) {
        control.disabled = true;
        const label = labelOf(control);
        if (busy && label) {
          if (label.dataset[IDLE] === undefined) label.dataset[IDLE] = label.textContent;
          label.textContent = busy;
        }
      }
    }, 0);
  });

  window.addEventListener("pageshow", () => {
    for (const form of document.querySelectorAll("form[data-submit-once]")) {
      delete form.dataset.submitOnceBusy;
      form.removeAttribute("aria-busy");
      for (const control of submitControlsOf(form)) {
        control.disabled = false;
        const label = labelOf(control);
        if (label && label.dataset[IDLE] !== undefined) {
          label.textContent = label.dataset[IDLE];
        }
      }
    }
  });
})();
