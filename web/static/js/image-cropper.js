// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Generic image cropping script using Cropper.js
 *
 * Supports two contexts:
 * 1. User profile photo (2:3 aspect ratio, portrait)   → input[name="foto"]
 * 2. Team profile photo (3:2 aspect ratio, landscape)  → input[name="team"]
 *
 * Automatically detects which context to use based on elements present in the DOM.
 * The resulting cropped input always receives name="foto_cropada" and is submitted with the form.
 */

(function() {
    'use strict';

    let cropper = null;
    let originalFileType = null;
    let context = null;

    /**
     * Detects the context based on elements present in the DOM.
     */
    function detectContext() {
        // User photo context (portrait 2:3)
        const fotoInput = document.querySelector('input[name="foto"]');
        if (fotoInput) {
            return {
                type: 'foto',
                input: fotoInput,
                modalId: 'cropModal',
                imageId: 'cropImage',
                previewId: 'cropPreview',
                confirmBtnId: 'cropConfirm',
                croppedFieldName: 'foto_cropada',
                aspectRatio: 2 / 3,  // Portrait
                maxWidth: 600,
                maxHeight: 900
            };
        }

        // Team photo context (landscape 16:10)
        const teamInput = document.querySelector('input[name="team"]');
        if (teamInput) {
            return {
                type: 'team',
                input: teamInput,
                modalId: 'cropModal',
                imageId: 'cropImage',
                previewId: 'cropPreview',
                confirmBtnId: 'cropConfirm',
                croppedFieldName: 'foto_cropada',
                aspectRatio: 16 / 10,  // Landscape
                maxWidth: 1600,
                maxHeight: 1000
            };
        }

        return null;
    }

    /**
     * Initializes the cropper.
     */
    function initCropper() {
        context = detectContext();
        if (!context) return;

        const modal = document.getElementById(context.modalId);
        const cropImage = document.getElementById(context.imageId);
        const cropPreview = document.getElementById(context.previewId);
        const confirmBtn = document.getElementById(context.confirmBtnId);

        if (!modal || !cropImage || !confirmBtn) return;

        const cropModal = new bootstrap.Modal(modal);

        // Listen for file input changes
        context.input.addEventListener('change', function(e) {
            const file = e.target.files[0];
            if (!file) return;

            // Validate that the selected file is an image
            if (!file.type.match('image.*')) {
                alert('Please, select a valid image file');
                context.input.value = '';
                return;
            }

            // Store the original MIME type
            originalFileType = file.type;

            // Read the file and open the modal
            const reader = new FileReader();
            reader.onload = function(event) {
                cropImage.src = event.target.result;
                cropModal.show();
            };
            reader.readAsDataURL(file);
        });

        // Initialize cropper when modal is shown
        modal.addEventListener('shown.bs.modal', function() {
            if (cropper) {
                cropper.destroy();
            }

            const cropperConfig = {
                aspectRatio: context.aspectRatio,
                viewMode: 1,
                autoCropArea: 1,
                responsive: true
            };

            // Add preview if the element exists
            if (cropPreview) {
                cropperConfig.preview = '#' + context.previewId;
            }

            cropper = new Cropper(cropImage, cropperConfig);
        });

        // Listen for crop confirmation
        confirmBtn.addEventListener('click', function() {
            if (!cropper) return;

            // Canvas config: limit max dimensions per context
            const canvasConfig = {
                maxWidth: context.maxWidth,
                maxHeight: context.maxHeight,
                imageSmoothingQuality: 'high'
            };

            const canvas = cropper.getCroppedCanvas(canvasConfig);

            // Determine format and quality
            const mimeType = originalFileType || 'image/jpeg';
            let quality = undefined;

            if (mimeType === 'image/jpeg' || mimeType === 'image/webp') {
                quality = 0.85;
            }

            // Convert to blob
            canvas.toBlob(function(blob) {
                const ext = mimeType.split('/')[1];
                const fileName = `${context.type}_cropped.${ext}`;
                const file = new File([blob], fileName, { type: mimeType });

                const dataTransfer = new DataTransfer();
                dataTransfer.items.add(file);

                // Create or reuse hidden input
                let croppedInput = document.querySelector(`input[name="${context.croppedFieldName}"]`);
                if (!croppedInput) {
                    croppedInput = document.createElement('input');
                    croppedInput.type = 'file';
                    croppedInput.name = context.croppedFieldName;
                    croppedInput.style.display = 'none';
                    context.input.form.appendChild(croppedInput);
                }
                croppedInput.files = dataTransfer.files;

                cropModal.hide();

                if (cropper) {
                    cropper.destroy();
                    cropper = null;
                }
            }, mimeType, quality);
        });

        // Clean up cropper when modal is closed
        modal.addEventListener('hidden.bs.modal', function() {
            if (cropper) {
                cropper.destroy();
                cropper = null;
            }
        });

        // Remove original input on submit if a cropped photo is present
        if (context.input.form) {
            context.input.form.addEventListener('submit', function() {
                const croppedInput = document.querySelector(`input[name="${context.croppedFieldName}"]`);
                if (croppedInput && croppedInput.files && croppedInput.files.length > 0) {
                    context.input.remove();
                }
            });
        }
    }

    // Initialize when the DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initCropper);
    } else {
        initCropper();
    }
})();
