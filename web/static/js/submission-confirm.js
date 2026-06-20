// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * submission-confirm.js
 * Handles syntax highlighting and the chief judge confirmation modal.
 * The Bootstrap modal is wired via data-bs-toggle/data-bs-target in the template.
 * The modal submit button uses form="confirm-form" to submit the form on confirmation.
 */

document.addEventListener('DOMContentLoaded', function () {
    const codeBlock = document.getElementById('submission-source-code');
    if (!codeBlock || !window.hljs) return;
    window.hljs.highlightElement(codeBlock);
    if (window.hljs.lineNumbersBlock) {
        window.hljs.lineNumbersBlock(codeBlock);
    }
});
