//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

document.querySelectorAll("tr[data-href]").forEach((row) => {
    row.addEventListener("click", (e) => {
        if (e.target.closest("a, button, .arena-favorite-icon")) return;
        window.location.href = row.dataset.href;
    });
});
