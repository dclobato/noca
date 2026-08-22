//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

document.addEventListener("DOMContentLoaded", () => {
  const passwordInput = document.getElementById("password");
  const passwordToggle = document.querySelector("[data-password-toggle]");
  const loginForm = document.querySelector("[data-login-form]");
  const loginSubmit = document.querySelector("[data-login-submit]");
  const submitLabel = document.querySelector("[data-login-submit-label]");
  const submitSpinner = document.querySelector("[data-login-spinner]");
  const loginStatus = document.querySelector("[data-login-status]");
  const loginError = document.querySelector("[data-login-error]");
  const flashAlert = document.querySelector("[data-login-flash] .alert");

  if (loginError instanceof HTMLElement) {
    loginError.focus();
  } else if (flashAlert instanceof HTMLElement) {
    flashAlert.setAttribute("tabindex", "-1");
    flashAlert.focus();
  }

  if (
    passwordInput instanceof HTMLInputElement &&
    passwordToggle instanceof HTMLButtonElement
  ) {
    passwordToggle.addEventListener("click", () => {
      const passwordIsVisible = passwordInput.type === "text";
      passwordInput.type = passwordIsVisible ? "password" : "text";
      passwordToggle.textContent = passwordIsVisible ? "Show" : "Hide";
      passwordToggle.setAttribute("aria-pressed", String(!passwordIsVisible));
      passwordInput.focus();
    });
  }

  if (
    loginForm instanceof HTMLFormElement &&
    loginSubmit instanceof HTMLButtonElement &&
    submitLabel instanceof HTMLElement &&
    submitSpinner instanceof HTMLElement
  ) {
    loginForm.addEventListener("submit", () => {
      loginForm.setAttribute("aria-busy", "true");
      loginSubmit.disabled = true;
      submitSpinner.classList.remove("d-none");
      submitLabel.textContent = "Signing in…";
      if (loginStatus instanceof HTMLElement) {
        loginStatus.textContent = "Signing in.";
      }
    });
  }
});

window.addEventListener("pageshow", () => {
  const loginForm = document.querySelector("[data-login-form]");
  const loginSubmit = document.querySelector("[data-login-submit]");
  const submitLabel = document.querySelector("[data-login-submit-label]");
  const submitSpinner = document.querySelector("[data-login-spinner]");
  const loginStatus = document.querySelector("[data-login-status]");

  if (loginForm instanceof HTMLFormElement) {
    loginForm.removeAttribute("aria-busy");
  }
  if (loginSubmit instanceof HTMLButtonElement) {
    loginSubmit.disabled = false;
  }
  if (submitLabel instanceof HTMLElement) {
    submitLabel.textContent = "Sign in";
  }
  if (submitSpinner instanceof HTMLElement) {
    submitSpinner.classList.add("d-none");
  }
  if (loginStatus instanceof HTMLElement) {
    loginStatus.textContent = "";
  }
});
