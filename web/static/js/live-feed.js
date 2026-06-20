// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Public contest live submission feed. The shared NocaLiveFeed engine owns the
// fetch/SSE/summary/fade machinery; this only renders contest-specific row
// cells, including the blackout-aware verdict cell.

(function () {
    'use strict';

    if (!window.NocaLiveFeed) {
        return;
    }

    function verdictCell(row, h) {
        if (row.frozen || row.verdict == null) {
            return '<span class="badge bg-secondary">frozen</span>';
        }
        return '<span class="badge ' + h.escapeHtml(row.verdict_badge_class) + '">' +
            h.escapeHtml(row.verdict) + '</span>';
    }

    NocaLiveFeed.init({
        renderRow: function (row, h) {
            return (
                '<td class="small text-muted text-nowrap">' + h.formatUtc(row.created_at) + '</td>' +
                '<td>' + h.escapeHtml(row.team) + '</td>' +
                '<td><span class="fw-semibold">' + h.escapeHtml(row.problem_label) + '</span> ' +
                h.escapeHtml(row.problem_name) + '</td>' +
                '<td><span class="d-flex align-items-center gap-1">' +
                '<img src="' + h.escapeHtml(row.language_icon_svg_url) + '" class="live-feed-lang-icon" alt="' + h.escapeHtml(row.language_name) + '">' +
                '<span class="small">' + h.escapeHtml(row.language_name) + '</span></span></td>' +
                '<td>' + verdictCell(row, h) + '</td>'
            );
        },
    });
})();
