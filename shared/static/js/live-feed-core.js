// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Shared live-feed engine for the web and Arena public submission feeds.
// Both feeds load a snapshot from feed.json and refetch it whenever the SSE
// channel signals a new verdict; the server is the sole data source. This
// module owns the fetch/SSE/status/debounce/known-row machinery plus the
// overflow summary line and trailing-row fade so the two pages cannot drift.
// Each page supplies only how a row's cells are rendered via
// NocaLiveFeed.init(options).

(function () {
    'use strict';

    function escapeHtml(value) {
        const div = document.createElement('div');
        div.textContent = value == null ? '' : String(value);
        return div.innerHTML;
    }

    function formatUtc(iso) {
        const date = new Date(iso);
        if (isNaN(date.getTime())) {
            return escapeHtml(iso);
        }
        const pad = (n) => String(n).padStart(2, '0');
        return (
            date.getUTCFullYear() + '-' + pad(date.getUTCMonth() + 1) + '-' + pad(date.getUTCDate()) +
            ' ' + pad(date.getUTCHours()) + ':' + pad(date.getUTCMinutes()) + ':' + pad(date.getUTCSeconds()) +
            ' UTC'
        );
    }

    // options:
    //   renderRow(row, helpers, ctx) -> string   required; inner <td> cells HTML
    //   colspan                                  optional; empty-state colspan (default 5)
    // helpers = { escapeHtml, formatUtc }; ctx = { index, total, data }.
    // The feed.json snapshot may carry `live_feed_limit` and `has_more`, which
    // drive the optional `#live-feed-summary` line and the trailing-row fade.
    function init(options) {
        const body = document.getElementById('live-feed-body');
        if (!body) {
            return;
        }
        const statusEl = document.getElementById('live-feed-status');
        const summaryEl = document.getElementById('live-feed-summary');
        const feedUrl = body.dataset.feedUrl;
        const eventsUrl = body.dataset.eventsUrl;
        const colspan = options.colspan || 5;
        const helpers = { escapeHtml: escapeHtml, formatUtc: formatUtc };

        let knownIds = new Set();
        let refetchTimer = null;

        function setStatus(text, cssClass) {
            if (!statusEl) {
                return;
            }
            statusEl.textContent = text;
            statusEl.className = 'badge ' + cssClass;
        }

        function updateSummary(rows, limit, hasMore) {
            if (!summaryEl) {
                return;
            }
            if (!rows.length) {
                summaryEl.textContent = 'Live feed is waiting for finalized submissions.';
                return;
            }
            const count = hasMore ? limit : rows.length;
            const noun = count === 1 ? 'submission' : 'submissions';
            summaryEl.textContent =
                'Live last ' + count + ' ' + noun + (hasMore ? ' — older entries continue below.' : '.');
        }

        function fadeClass(index, total, hasMore) {
            if (!hasMore || total < 4) {
                return null;
            }
            const remaining = total - index;
            return remaining <= 3 ? 'live-feed-row-fade-' + remaining : null;
        }

        function render(data) {
            const rows = (data && data.submissions) || [];
            const hasMore = Boolean(data && data.has_more);
            const limit = (data && data.live_feed_limit) || rows.length;
            body.innerHTML = '';
            if (!rows.length) {
                body.innerHTML = '<tr><td colspan="' + colspan +
                    '" class="text-center text-muted">No submissions yet.</td></tr>';
            } else {
                rows.forEach((row, index) => {
                    const tr = document.createElement('tr');
                    if (!knownIds.has(row.submission_id)) {
                        tr.classList.add('live-feed-row-new');
                    }
                    const fade = fadeClass(index, rows.length, hasMore);
                    if (fade) {
                        tr.classList.add(fade);
                    }
                    tr.innerHTML = options.renderRow(row, helpers, { index: index, total: rows.length, data: data });
                    body.appendChild(tr);
                });
            }
            updateSummary(rows, limit, hasMore);
            knownIds = new Set(rows.map((row) => row.submission_id));
        }

        async function refresh() {
            try {
                const response = await fetch(feedUrl, { headers: { 'Accept': 'application/json' } });
                if (!response.ok) {
                    throw new Error('HTTP ' + response.status);
                }
                render(await response.json());
            } catch (err) {
                console.error('Live feed refresh failed', err);
            }
        }

        function scheduleRefresh() {
            if (refetchTimer) {
                return;
            }
            refetchTimer = setTimeout(() => {
                refetchTimer = null;
                refresh();
            }, 250);
        }

        function connect() {
            const source = new EventSource(eventsUrl);
            source.onopen = () => setStatus('Live', 'bg-success');
            source.onmessage = (event) => {
                if (event.data === 'refresh') {
                    scheduleRefresh();
                }
            };
            source.onerror = () => setStatus('Reconnecting…', 'bg-warning text-dark');
        }

        refresh();
        connect();
    }

    window.NocaLiveFeed = { init: init };
})();
