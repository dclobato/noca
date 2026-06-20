/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

(function () {
    "use strict";

    const dropzone = document.getElementById("profile-photo-dropzone");
    const fileInput = document.getElementById("profile_photo");
    const placeholder = document.getElementById("profile-photo-placeholder");
    const preview = document.getElementById("profile-photo-preview");
    const previewImg = document.getElementById("profile-photo-img");
    const filenameEl = document.getElementById("profile-photo-filename");
    const dateOfBirthInput = document.getElementById("date_of_birth");
    const parentalEmailGroup = document.getElementById("parental-email-group");
    const parentalEmailInput = document.getElementById("email_responsavel_legal");

    if (dateOfBirthInput && parentalEmailGroup && parentalEmailInput) {
        dateOfBirthInput.addEventListener("change", updateParentalEmailVisibility);
        updateParentalEmailVisibility();
    }

    if (!dropzone || !fileInput) return;

    dropzone.addEventListener("click", function () {
        fileInput.click();
    });

    fileInput.addEventListener("change", function () {
        handleFile(this.files && this.files[0]);
    });

    dropzone.addEventListener("dragover", function (e) {
        e.preventDefault();
        dropzone.classList.add("drag-over");
    });

    dropzone.addEventListener("dragleave", function (e) {
        e.preventDefault();
        dropzone.classList.remove("drag-over");
    });

    dropzone.addEventListener("drop", function (e) {
        e.preventDefault();
        dropzone.classList.remove("drag-over");
        const files = e.dataTransfer && e.dataTransfer.files;
        if (files && files.length > 0) {
            fileInput.files = files;
            handleFile(files[0]);
        }
    });

    function handleFile(file) {
        if (!file) return;

        // When the crop modal is present, arena-image-cropper.js handles the preview
        if (document.getElementById("cropModal")) return;

        if (!file.type.match(/^image\/(png|jpeg)$/)) {
            alert("Only PNG and JPG images are allowed.");
            return;
        }
        if (file.size > 2 * 1024 * 1024) {
            alert("Image must be smaller than 2MB.");
            return;
        }

        placeholder.classList.add("d-none");
        preview.classList.remove("d-none");
        previewImg.src = URL.createObjectURL(file);
        filenameEl.textContent = file.name;
    }

    function updateParentalEmailVisibility() {
        const birthDate = parseDateInput(dateOfBirthInput.value);
        if (!birthDate) {
            parentalEmailGroup.classList.add("d-none");
            parentalEmailInput.required = false;
            return;
        }

        const age = calculateAgeYears(birthDate, new Date());
        const needsConsent = age >= 13 && age < 18;
        parentalEmailGroup.classList.toggle("d-none", !needsConsent);
        parentalEmailInput.required = needsConsent;
    }

    function parseDateInput(value) {
        if (!value) return null;
        const parts = value.split("-").map(Number);
        if (parts.length !== 3 || parts.some(Number.isNaN)) return null;
        return new Date(parts[0], parts[1] - 1, parts[2]);
    }

    function calculateAgeYears(birthDate, today) {
        let age = today.getFullYear() - birthDate.getFullYear();
        const birthdayThisYear = new Date(today.getFullYear(), birthDate.getMonth(), birthDate.getDate());
        if (today < birthdayThisYear) age -= 1;
        return age;
    }
})();
