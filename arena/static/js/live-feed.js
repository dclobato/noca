// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
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
            const affiliationLogo = row.affiliation_logo_url
                ? '<img src="' + h.escapeHtml(row.affiliation_logo_url) +
                    '" class="affiliation-logo-thumb" alt="">'
                : '';
            const affiliation = row.affiliation_name
                ? '<span class="arena-live-feed-affiliation">' + affiliationLogo +
                    '<span class="arena-live-feed-affiliation-name" title="' +
                    h.escapeHtml(row.affiliation_name) + '">' +
                    h.escapeHtml(row.affiliation_name) + '</span></span>'
                : '<span class="text-muted" aria-label="No affiliation">—</span>';
            const countryName = row.country_name || row.country_code || 'Country';
            const countryFlagClass = row.country_code === 'BR'
                ? 'arena-country-flag arena-country-flag--circular'
                : 'arena-country-flag';
            const country = row.country_flag_url
                ? '<img src="' + h.escapeHtml(row.country_flag_url) +
                    '" class="' + countryFlagClass + '" alt="' +
                    h.escapeHtml(countryName) + '" title="' +
                    h.escapeHtml(countryName) + '">'
                : '';
            const stateName = row.subdivision_name || row.subdivision_code ||
                'Brazilian state';
            const state = row.state_flag_url
                ? '<img src="' + h.escapeHtml(row.state_flag_url) +
                    '" class="arena-live-feed-state-flag" alt="' +
                    h.escapeHtml(stateName) + '" title="' + h.escapeHtml(stateName) + '">'
                : '';
            const origin = country || state
                ? '<span class="arena-live-feed-origin">' + country + state + '</span>'
                : '<span class="text-muted" aria-label="No origin">—</span>';
            return (
                '<td class="small text-muted text-nowrap arena-live-feed-col-date">' +
                h.escapeHtml(row.created_at_display || h.formatUtc(row.created_at)) + '</td>' +
                '<td class="arena-live-feed-col-affiliation">' + affiliation + '</td>' +
                '<td class="arena-live-feed-col-origin">' + origin + '</td>' +
                '<td>' + problem + '</td>' +
                '<td><span class="d-flex align-items-center gap-1">' +
                '<img src="' + h.escapeHtml(row.language_icon_svg_url) +
                '" class="live-feed-lang-icon" alt="' +
                h.escapeHtml(row.language_name) + '">' +
                '<span class="small">' + h.escapeHtml(row.language_name) + '</span></span></td>' +
                '<td><span class="badge ' + h.escapeHtml(row.verdict_badge_class) + '">' +
                h.escapeHtml(row.verdict_label) + '</span></td>'
            );
        },
    });
})();
