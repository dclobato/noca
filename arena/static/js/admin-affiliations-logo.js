/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/**
 * Affiliation logo modal controller.
 *
 * Handles the standalone #affiliation-logo-modal with an inline Cropper.js
 * widget.  No second modal is ever opened; the cropper lives inside this
 * single modal, avoiding Bootstrap nested-modal interference.
 *
 * Responsibilities:
 *   1. Camera-button click — populate modal state and open it.
 *   2. Modal lifecycle — reset state on hidden.bs.modal.
 *   3. File input change — init inline Cropper.js.
 *   4. Remove-logo button — set pending-remove flag, update preview UI.
 *   5. Restore-logo button — cancel pending-remove, restore preview UI.
 *   6. Save button — POST via AJAX; update row thumbnail; close modal.
 *   7. Save button enable/disable based on pending action.
 */

(() => {
    "use strict";

    const CROPPER_OPTIONS = {
        aspectRatio: 1,
        viewMode: 1,
        autoCropArea: 0.9,
        movable: true,
        zoomable: true,
        rotatable: false,
        scalable: false,
        preview: "#logo-modal-crop-preview",
    };

    // ── DOM refs ──────────────────────────────────────────────────────────────
    const modalEl = document.getElementById("affiliation-logo-modal");
    if (!modalEl) return;

    const modalTitleEl = document.getElementById("affiliation-logo-modal-title");
    const affiliationNameEl = document.getElementById("logo-modal-affiliation-name");
    const previewImg = document.getElementById("logo-modal-preview");
    const noLogoText = document.getElementById("logo-modal-no-logo");
    const cropArea = document.getElementById("logo-modal-crop-area");
    const cropImage = document.getElementById("logo-modal-crop-image");
    const errorEl = document.getElementById("logo-modal-error");
    const fileInput = document.getElementById("logo-modal-file-input");
    const selectBtn = document.getElementById("logo-modal-select-btn");
    const removeBtn = document.getElementById("logo-modal-remove-btn");
    const restoreBtn = document.getElementById("logo-modal-restore-btn");
    const saveBtn = document.getElementById("logo-modal-save");

    // ── State ─────────────────────────────────────────────────────────────────
    /** @type {Cropper|null} */
    let cropper = null;
    /** @type {string} Currently loaded affiliation ID */
    let currentAffiliationId = "";
    /** @type {string} URL of the save endpoint */
    let currentSaveUrl = "";
    /** @type {string} Original logo URL (empty string = no logo) */
    let originalLogoUrl = "";
    /** @type {boolean} User has staged a remove-logo action */
    let pendingRemove = false;
    /** @type {boolean} User has staged a new image (cropper active) */
    let pendingNewImage = false;

    // ── Helpers ───────────────────────────────────────────────────────────────

    /** Destroy the Cropper.js instance if one is active. */
    function destroyCropper() {
        if (cropper) {
            cropper.destroy();
            cropper = null;
        }
    }

    /** Hide crop area and reset its image src. */
    function hideCropArea() {
        cropArea.classList.add("d-none");
        cropImage.src = "";
    }

    /** Show an error message inside the modal. */
    function showError(message) {
        errorEl.textContent = message;
        errorEl.classList.remove("d-none");
    }

    /** Clear any displayed error. */
    function clearError() {
        errorEl.textContent = "";
        errorEl.classList.add("d-none");
    }

    /**
     * Update save-button enabled state: enabled only when a concrete action
     * is pending (new image ready or remove staged).
     */
    function updateSaveEnabled() {
        saveBtn.disabled = !(pendingNewImage || pendingRemove);
    }

    /**
     * Render the current-logo section for a given logo URL.
     *
     * @param {string} logoUrl - Logo URL, or empty string for "no logo".
     */
    function renderCurrentLogo(logoUrl) {
        if (logoUrl) {
            previewImg.src = logoUrl;
            previewImg.classList.remove("d-none");
            noLogoText.classList.add("d-none");
            removeBtn.classList.remove("d-none");
            restoreBtn.classList.add("d-none");
        } else {
            previewImg.src = "";
            previewImg.classList.add("d-none");
            noLogoText.classList.remove("d-none");
            removeBtn.classList.add("d-none");
            restoreBtn.classList.add("d-none");
        }
    }

    /**
     * Reset all modal state to a clean slate.  Called on hidden.bs.modal.
     */
    function resetModal() {
        destroyCropper();
        hideCropArea();
        clearError();
        renderCurrentLogo("");
        fileInput.value = "";
        currentAffiliationId = "";
        currentSaveUrl = "";
        originalLogoUrl = "";
        pendingRemove = false;
        pendingNewImage = false;
        saveBtn.disabled = true;
        saveBtn.textContent = "";
        // Restore save button label (icon was removed by text reset)
        saveBtn.innerHTML =
            '<span class="material-symbols-outlined" aria-hidden="true">save</span> Save';
    }

    // ── Camera button wiring ──────────────────────────────────────────────────
    document.querySelectorAll("[data-affiliation-logo-button]").forEach((btn) => {
        btn.addEventListener("click", () => {
            currentAffiliationId = btn.getAttribute("data-affiliation-id") || "";
            currentSaveUrl = btn.getAttribute("data-save-url") || "";
            originalLogoUrl = btn.getAttribute("data-logo-url") || "";
            pendingRemove = false;
            pendingNewImage = false;

            const affName = btn.getAttribute("data-affiliation-name") || currentAffiliationId;
            if (affiliationNameEl) affiliationNameEl.textContent = affName;

            renderCurrentLogo(originalLogoUrl);
            clearError();
            hideCropArea();
            fileInput.value = "";
            saveBtn.disabled = true;

            bootstrap.Modal.getOrCreateInstance(modalEl).show();
        });
    });

    // ── Modal lifecycle ───────────────────────────────────────────────────────
    modalEl.addEventListener("hidden.bs.modal", resetModal);

    // ── Select image button ───────────────────────────────────────────────────
    selectBtn.addEventListener("click", () => {
        fileInput.click();
    });

    // ── File input change ─────────────────────────────────────────────────────
    fileInput.addEventListener("change", () => {
        const file = fileInput.files?.[0];
        if (!file) return;

        clearError();
        destroyCropper();

        const objectUrl = URL.createObjectURL(file);
        cropImage.src = objectUrl;
        cropArea.classList.remove("d-none");

        // Hide the current-logo preview while cropping
        previewImg.classList.add("d-none");
        noLogoText.classList.add("d-none");

        // Cancel a pending remove when user picks a new file
        pendingRemove = false;
        removeBtn.classList.add("d-none");
        restoreBtn.classList.add("d-none");

        cropImage.onload = () => {
            cropper = new Cropper(cropImage, CROPPER_OPTIONS);
            pendingNewImage = true;
            updateSaveEnabled();
        };
    });

    // ── Remove logo button ────────────────────────────────────────────────────
    removeBtn.addEventListener("click", () => {
        // Cancel any in-progress crop
        destroyCropper();
        hideCropArea();
        fileInput.value = "";
        pendingNewImage = false;

        pendingRemove = true;
        previewImg.classList.add("d-none");
        noLogoText.classList.remove("d-none");
        removeBtn.classList.add("d-none");
        restoreBtn.classList.remove("d-none");
        clearError();
        updateSaveEnabled();
    });

    // ── Restore logo button ───────────────────────────────────────────────────
    restoreBtn.addEventListener("click", () => {
        pendingRemove = false;
        renderCurrentLogo(originalLogoUrl);
        clearError();
        updateSaveEnabled();
    });

    // ── Save button ───────────────────────────────────────────────────────────
    saveBtn.addEventListener("click", async () => {
        if (!currentSaveUrl) return;
        clearError();
        saveBtn.disabled = true;

        try {
            const formData = new FormData();

            if (pendingRemove) {
                formData.append("remove_logo", "1");
            } else if (pendingNewImage && cropper) {
                const canvas = cropper.getCroppedCanvas({ width: 512, height: 512 });
                const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.9));
                formData.append("foto_cropada", blob, "logo.jpg");
            } else {
                showError("No action selected.");
                saveBtn.disabled = false;
                return;
            }

            const res = await fetch(currentSaveUrl, { method: "POST", body: formData });
            const data = await res.json();

            if (!data.ok) {
                showError(data.error || "An error occurred. Please try again.");
                saveBtn.disabled = false;
                return;
            }

            // Update the row thumbnail in the table
            const thumbContainer = document.querySelector(
                `[data-affiliation-logo-thumb="${currentAffiliationId}"]`,
            );
            if (thumbContainer) {
                if (data.logo_url) {
                    // Replace or create the <img> thumbnail
                    let img = thumbContainer;
                    if (thumbContainer.tagName !== "IMG") {
                        // was a placeholder <span>, replace with <img>
                        const newImg = document.createElement("img");
                        newImg.alt = "";
                        newImg.className = "me-2 affiliation-logo-thumb";
                        newImg.setAttribute("data-affiliation-logo-thumb", currentAffiliationId);
                        thumbContainer.replaceWith(newImg);
                        img = newImg;
                    }
                    // Force cache-bust so the browser reloads the updated image
                    img.src = `${data.logo_url}?t=${Date.now()}`;
                    img.classList.remove("d-none");
                } else {
                    // Remove the thumbnail: replace img with hidden span
                    const span = document.createElement("span");
                    span.className = "affiliation-logo-thumb d-none";
                    span.setAttribute("data-affiliation-logo-thumb", currentAffiliationId);
                    thumbContainer.replaceWith(span);
                }
            }

            // Update the camera button's data-logo-url for next open
            const cameraBtn = document.querySelector(
                `[data-affiliation-logo-button][data-affiliation-id="${currentAffiliationId}"]`,
            );
            if (cameraBtn) {
                cameraBtn.setAttribute("data-logo-url", data.logo_url || "");
            }

            bootstrap.Modal.getOrCreateInstance(modalEl).hide();
        } catch (err) {
            showError("Network error. Please try again.");
            saveBtn.disabled = false;
        }
    });
})();
