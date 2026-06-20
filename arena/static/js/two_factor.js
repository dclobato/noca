/*
 *  NOCA -- Next Online Contest Administrator
 *  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

(function () {
    "use strict";

    var otpContainer = document.getElementById("otp-inputs");
    if (!otpContainer) return;
    var inputs = Array.from(otpContainer.querySelectorAll(".arena-otp-input"));

    inputs.forEach(function (input, index) {
        input.addEventListener("input", function () {
            var val = input.value;
            if (/^[A-Za-z0-9]$/.test(val)) {
                input.value = val;
                if (index < inputs.length - 1) {
                    inputs[index + 1].focus();
                }
            } else {
                input.value = "";
            }
        });

        input.addEventListener("keydown", function (e) {
            if (e.key === "Backspace") {
                if (input.value === "" && index > 0) {
                    inputs[index - 1].focus();
                }
            }
        });

        input.addEventListener("paste", function (e) {
            e.preventDefault();
            var pasted = (e.clipboardData || window.clipboardData).getData("text");
            var digits = pasted.replace(/[^A-Za-z0-9]/g, "").slice(0, inputs.length);
            digits.split("").forEach(function (digit, i) {
                inputs[i].value = digit;
            });
            if (digits.length > 0) {
                var focusIdx = Math.min(digits.length, inputs.length - 1);
                inputs[focusIdx].focus();
            }
        });
    });

    var form = document.getElementById("otp-form");
    if (form) {
        form.addEventListener("submit", function () {
            var fullCode = inputs.map(function (input) {
                return input.value;
            }).join("");
            var hiddenInput = document.getElementById("full-code");
            if (hiddenInput) {
                hiddenInput.value = fullCode;
            }
            inputs.forEach(function (input) {
                input.removeAttribute("name");
            });
        });
    }
})();