// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/** Preview selected audio clips and mark staged media as unsaved. */

(function() {
    'use strict';

    const previewUrls = [];

    document.querySelectorAll('input[type="file"][data-audio-preview-id]').forEach(function(input) {
        input.addEventListener('change', function() {
            const file = input.files && input.files[0];
            if (!file) return;

            const preview = document.getElementById(input.dataset.audioPreviewId);
            if (!preview) return;

            const previewUrl = URL.createObjectURL(file);
            previewUrls.push(previewUrl);
            preview.src = previewUrl;
            preview.classList.remove('d-none');
            preview.load();

            const emptyState = document.getElementById(input.dataset.audioEmptyStateId || '');
            if (emptyState) emptyState.classList.add('d-none');

            const indicator = document.getElementById(input.dataset.unsavedIndicatorId || '');
            if (indicator) indicator.classList.remove('d-none');
        });
    });

    window.addEventListener('pagehide', function() {
        previewUrls.forEach(function(url) {
            URL.revokeObjectURL(url);
        });
    });
})();
