// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Arena public live submission feed. The shared NocaLiveFeed engine owns the
// fetch/SSE/summary/fade machinery; this only renders Arena-specific row cells.

(function () {
    'use strict';

    if (!window.NocaLiveFeed) {
        return;
    }

    NocaLiveFeed.init({
        renderRow: function (row, h) {
            const problem = '<a href="' + h.escapeHtml(row.problem_url) + '">' +
                h.escapeHtml(row.problem_number) + ': ' + h.escapeHtml(row.problem_title) + '</a>';
            const affiliation = row.affiliation_logo_url
                ? '<img src="' + h.escapeHtml(row.affiliation_logo_url) +
                    '" class="affiliation-logo-thumb" alt="' +
                    h.escapeHtml(row.affiliation_name) + '" title="' +
                    h.escapeHtml(row.affiliation_name) + '">'
                : '<span class="text-muted" aria-label="No affiliation logo">-</span>';
            const country = row.country_flag_url
                ? '<img src="' + h.escapeHtml(row.country_flag_url) +
                    '" class="arena-country-flag" alt="' +
                    h.escapeHtml(row.country_name) + '" title="' +
                    h.escapeHtml(row.country_name) + '">'
                : '<span class="text-muted" aria-label="No location country">-</span>';
            return (
                '<td class="small text-muted text-nowrap">' +
                h.escapeHtml(row.created_at_display || h.formatUtc(row.created_at)) + '</td>' +
                '<td><span class="d-flex align-items-center gap-2">' +
                affiliation + country + '</span></td>' +
                '<td>' + problem + '</td>' +
                '<td><span class="d-flex align-items-center gap-1">' +
                '<img src="' + h.escapeHtml(row.language_icon_svg_url) + '" class="live-feed-lang-icon" alt="' + h.escapeHtml(row.language_name) + '">' +
                '<span class="small">' + h.escapeHtml(row.language_name) + '</span></span></td>' +
                '<td><span class="badge ' + h.escapeHtml(row.verdict_badge_class) + '">' +
                h.escapeHtml(row.verdict_label) + '</span></td>'
            );
        },
    });
})();
