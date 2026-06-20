// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Arena image cropping module — multi-instance, data-attribute-driven.
 *
 * Handles any number of file inputs that need a Cropper.js crop flow before
 * submitting.  Each file input is wired by calling ``initImageCropper(input)``
 * or via the auto-init at the bottom of this file.
 *
 * Supported data attributes on the <input type="file"> element:
 *
 *   data-crop-modal-id        ID of the Bootstrap modal containing the cropper.
 *   data-crop-image-id        ID of the <img> tag inside the modal.
 *   data-crop-confirm-id      ID of the confirm/crop button inside the modal.
 *   data-crop-preview-id      ID of a live Cropper.js preview div (optional).
 *   data-crop-aspect-ratio    Aspect ratio string, e.g. "2/3" or "1/1". Default "2/3".
 *   data-crop-max-width       Maximum canvas width for getCroppedCanvas. Default 600.
 *   data-crop-max-height      Maximum canvas height for getCroppedCanvas. Default 900.
 *   data-crop-output-name     name attribute for the hidden file input. Default "foto_cropada".
 *   data-crop-auto-submit     "true" to auto-submit the form after cropping. Default "false".
 *   data-crop-placeholder-id  ID of an element to hide when a file is selected (dropzone style).
 *   data-crop-preview-el-id   ID of an element to show after cropping (dropzone style).
 *   data-crop-filename-id     ID of an element whose textContent is set to the file name.
 *
 * Multiple inputs can share a single crop modal by giving them the same
 * data-crop-modal-id.  The last input that triggered a file-selection will
 * receive the cropped output.
 *
 * Backward-compatible auto-init for pages that use the old-style
 * input[name="profile_photo"] without any data-crop-* attributes is
 * performed at the bottom of this file.
 */

(function () {
    "use strict";

    // ── Per-modal shared state ────────────────────────────────────────────────
    // Keyed by crop modal element ID.
    const _croppers = {};      // modalId → Cropper instance (or null)
    const _fileTypes = {};     // modalId → MIME type of the currently selected file
    const _activeInputs = {};  // modalId → the file input that last triggered a selection
    const _initialized = {};   // modalId → true once modal listeners are attached

    // ── Utilities ─────────────────────────────────────────────────────────────

    /**
     * Parse an aspect-ratio string like "2/3" or "1/1" into a number.
     *
     * @param {string} str - Ratio string.
     * @returns {number} Numeric aspect ratio.
     */
    function _parseAspectRatio(str) {
        if (!str || !str.includes("/")) return 2 / 3;
        const parts = str.split("/");
        const w = parseFloat(parts[0]);
        const h = parseFloat(parts[1]);
        if (!w || !h) return 2 / 3;
        return w / h;
    }

    /**
     * Update dropzone-style preview elements after a successful crop.
     *
     * All parameters are IDs; all are optional — missing elements are silently skipped.
     *
     * @param {string|null} placeholderId - Element to add "d-none" to.
     * @param {string|null} previewElId   - Element to remove "d-none" from.
     * @param {string|null} filenameId    - Element whose textContent is set to fileName.
     * @param {string}      fileName      - Cropped file name.
     */
    function _updateDropzoneUI(placeholderId, previewElId, filenameId, fileName) {
        if (placeholderId) {
            const el = document.getElementById(placeholderId);
            if (el) el.classList.add("d-none");
        }
        if (previewElId) {
            const el = document.getElementById(previewElId);
            if (el) el.classList.remove("d-none");
        }
        if (filenameId) {
            const el = document.getElementById(filenameId);
            if (el) el.textContent = fileName;
        }
    }

    // ── Core initialiser ──────────────────────────────────────────────────────

    /**
     * Wire up a file input to a Cropper.js crop modal.
     *
     * All options are read from data-* attributes on ``input``; see the
     * module-level docstring for the full list.
     *
     * @param {HTMLInputElement} input - The file input to wire.
     */
    function initImageCropper(input) {
        const modalId = input.dataset.cropModalId || "cropModal";
        const imageId = input.dataset.cropImageId || "cropImage";
        const confirmId = input.dataset.cropConfirmId || "cropConfirm";

        const modalEl = document.getElementById(modalId);
        const cropImage = document.getElementById(imageId);
        const confirmBtn = document.getElementById(confirmId);
        if (!modalEl || !cropImage || !confirmBtn) return;

        // ── File-selection handler ────────────────────────────────────────────
        input.addEventListener("change", function (e) {
            const file = e.target.files[0];
            if (!file) return;
            if (!file.type.match("image.*")) {
                alert("Please select a valid image file.");
                input.value = "";
                return;
            }
            _activeInputs[modalId] = input;
            _fileTypes[modalId] = file.type;

            const reader = new FileReader();
            reader.onload = function (ev) {
                cropImage.src = ev.target.result;
                bootstrap.Modal.getOrCreateInstance(modalEl).show();
            };
            reader.readAsDataURL(file);
        });

        // ── Modal listeners (attached once per modal ID) ──────────────────────
        if (_initialized[modalId]) return;
        _initialized[modalId] = true;

        modalEl.addEventListener("shown.bs.modal", function () {
            if (_croppers[modalId]) {
                _croppers[modalId].destroy();
            }

            const activeInput = _activeInputs[modalId];
            const ratio = activeInput
                ? _parseAspectRatio(activeInput.dataset.cropAspectRatio || "2/3")
                : 2 / 3;
            const previewId = activeInput ? (activeInput.dataset.cropPreviewId || null) : null;

            const config = {
                aspectRatio: ratio,
                viewMode: 1,
                autoCropArea: 1,
                responsive: true,
            };
            if (previewId && document.getElementById(previewId)) {
                config.preview = "#" + previewId;
            }
            _croppers[modalId] = new Cropper(cropImage, config);
        });

        confirmBtn.addEventListener("click", function () {
            const cropper = _croppers[modalId];
            if (!cropper) return;

            const activeInput = _activeInputs[modalId];
            if (!activeInput) return;
            const form = activeInput.closest("form");
            if (!form) return;

            const maxWidth = parseInt(activeInput.dataset.cropMaxWidth || "600", 10);
            const maxHeight = parseInt(activeInput.dataset.cropMaxHeight || "900", 10);
            const outputName = activeInput.dataset.cropOutputName || "foto_cropada";
            const autoSubmit = activeInput.dataset.cropAutoSubmit === "true";
            const mimeType = _fileTypes[modalId] || "image/jpeg";
            const quality =
                mimeType === "image/jpeg" || mimeType === "image/webp" ? 0.85 : undefined;

            const canvas = cropper.getCroppedCanvas({
                maxWidth: maxWidth,
                maxHeight: maxHeight,
                imageSmoothingQuality: "high",
            });

            canvas.toBlob(
                function (blob) {
                    const ext = mimeType.split("/")[1] || "jpg";
                    const file = new File([blob], "arena_cropped." + ext, { type: mimeType });
                    const dt = new DataTransfer();
                    dt.items.add(file);

                    let croppedInput = form.querySelector('input[name="' + outputName + '"]');
                    if (!croppedInput) {
                        croppedInput = document.createElement("input");
                        croppedInput.type = "file";
                        croppedInput.name = outputName;
                        croppedInput.style.display = "none";
                        form.appendChild(croppedInput);
                    }
                    croppedInput.files = dt.files;

                    bootstrap.Modal.getInstance(modalEl)?.hide();

                    if (_croppers[modalId]) {
                        _croppers[modalId].destroy();
                        _croppers[modalId] = null;
                    }

                    _updateDropzoneUI(
                        activeInput.dataset.cropPlaceholderId || null,
                        activeInput.dataset.cropPreviewElId || null,
                        activeInput.dataset.cropFilenameId || null,
                        file.name,
                    );

                    if (autoSubmit) {
                        form.requestSubmit();
                    }
                },
                mimeType,
                quality,
            );
        });

        modalEl.addEventListener("hidden.bs.modal", function () {
            if (_croppers[modalId]) {
                _croppers[modalId].destroy();
                _croppers[modalId] = null;
            }
        });
    }

    // ── Before-submit: strip raw file input when cropped version is ready ─────
    document.querySelectorAll("form").forEach(function (form) {
        form.addEventListener("submit", function () {
            form.querySelectorAll('input[type="file"][data-crop-modal-id]').forEach(function (inp) {
                const outputName = inp.dataset.cropOutputName || "foto_cropada";
                const cropped = form.querySelector('input[name="' + outputName + '"]');
                if (cropped && cropped.files && cropped.files.length > 0) {
                    inp.remove();
                }
            });
        });
    });

    // ── Auto-init: inputs with explicit data-crop-modal-id ───────────────────
    document.querySelectorAll('input[type="file"][data-crop-modal-id]').forEach(initImageCropper);

    // ── Backward compat: profile_photo without data-crop-* attributes ─────────
    // Both the signup form and the profile update form use input[name="profile_photo"]
    // with the old hard-coded IDs.  We inject the data attributes here so they
    // go through the same factory path as every other input.
    document.querySelectorAll('input[name="profile_photo"]:not([data-crop-modal-id])').forEach(
        function (input) {
            input.dataset.cropModalId = "cropModal";
            input.dataset.cropImageId = "cropImage";
            input.dataset.cropConfirmId = "cropConfirm";
            input.dataset.cropPreviewId = "cropPreview";
            input.dataset.cropAspectRatio = "2/3";
            input.dataset.cropMaxWidth = "600";
            input.dataset.cropMaxHeight = "900";
            input.dataset.cropOutputName = "foto_cropada";
            input.dataset.cropPlaceholderId = "profile-photo-placeholder";
            input.dataset.cropPreviewElId = "profile-photo-preview";
            input.dataset.cropFilenameId = "profile-photo-filename";
            // Auto-submit only on the dedicated profile-photo-form.
            if (input.closest("form") && input.closest("form").id === "profile-photo-form") {
                input.dataset.cropAutoSubmit = "true";
            }
            initImageCropper(input);
        },
    );

    // Expose for programmatic use (e.g. after dynamic modal injection).
    window.arenaInitImageCropper = initImageCropper;
})();
