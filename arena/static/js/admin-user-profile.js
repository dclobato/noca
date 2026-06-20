//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

(function () {
    'use strict';

    // Sync selected role → change-role modal before it opens
    const changeRoleModal = document.getElementById('confirmChangeRoleModal');
    if (changeRoleModal) {
        const roleSelect = document.getElementById('admin-role-select');
        const hiddenRole = document.getElementById('role-modal-new-role');
        const roleNameSpan = document.getElementById('role-modal-role-name');

        changeRoleModal.addEventListener('show.bs.modal', function () {
            if (!roleSelect) return;
            const opt = roleSelect.options[roleSelect.selectedIndex];
            if (hiddenRole) hiddenRole.value = opt.value;
            if (roleNameSpan) roleNameSpan.textContent = opt.text;
        });
    }

    // Clear password inputs whenever any confirm modal closes
    const confirmModals = [
        'confirmToggleActiveModal',
        'confirmForcePwChangeModal',
        'confirmChangeRoleModal',
        'confirmToggleCanEditModal',
        'confirmDisable2FAProfileModal',
    ];
    confirmModals.forEach(function (id) {
        const modal = document.getElementById(id);
        if (!modal) return;
        modal.addEventListener('hidden.bs.modal', function () {
            modal.querySelectorAll('input[type="password"]').forEach(function (input) {
                input.value = '';
            });
        });
    });
})();
