// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Renders the "Contest timing overview" bar in two modes:
//
// * Form mode (contest create/edit): values come live from the
//   duration_minutes / stop_updating_scoreboard / stop_answers_after inputs,
//   re-rendered on every input event.
// * Static mode (contest dashboard rules banner): the form inputs are absent
//   and the values arrive as data attributes on #timing-timeline-wrapper, so
//   the bar renders once on load.

(function () {
    'use strict';

    var WRAPPER = document.getElementById('timing-timeline-wrapper');
    var SEG_FALLBACK = document.getElementById('timeline-seg-fallback');
    var SEG_LIVE = document.getElementById('timeline-seg-live');
    var SEG_FROZEN = document.getElementById('timeline-seg-frozen');
    var SEG_SILENCE = document.getElementById('timeline-seg-silence');

    if (!WRAPPER || !SEG_FALLBACK || !SEG_LIVE || !SEG_FROZEN || !SEG_SILENCE) return;

    function sanitize(value) {
        return isNaN(value) || value <= 0 ? NaN : value;
    }

    function getInt(id, dataAttr) {
        var el = document.getElementById(id);
        if (el) return sanitize(parseInt(el.value, 10));
        return sanitize(parseInt(WRAPPER.getAttribute(dataAttr), 10));
    }

    function setSegment(segEl, pct) {
        if (pct <= 0) {
            hideSegment(segEl);
            return;
        }
        segEl.style.width = pct.toFixed(4) + '%';
        segEl.classList.remove('d-none', 'timing-timeline-seg-full');
        segEl.setAttribute('aria-valuenow', pct.toFixed(4));
    }

    function hideSegment(segEl) {
        segEl.style.width = '';
        segEl.classList.add('d-none');
        segEl.classList.remove('timing-timeline-seg-full');
    }

    function showFallback() {
        SEG_FALLBACK.style.width = '';
        SEG_FALLBACK.classList.remove('d-none');
        SEG_FALLBACK.classList.add('timing-timeline-seg-full');
        hideSegment(SEG_LIVE);
        hideSegment(SEG_FROZEN);
        hideSegment(SEG_SILENCE);
    }

    function showPhases(pLive, pFrozen, pSilence) {
        SEG_FALLBACK.classList.add('d-none');
        setSegment(SEG_LIVE, pLive);
        setSegment(SEG_FROZEN, pFrozen);
        setSegment(SEG_SILENCE, pSilence);
    }

    function update() {
        var duration = getInt('duration_minutes', 'data-duration-minutes');
        var stopScoreboard = getInt('stop_updating_scoreboard', 'data-freeze-minutes');
        var stopAnswers = getInt('stop_answers_after', 'data-blind-minutes');

        var invalid =
            isNaN(duration) ||
            isNaN(stopScoreboard) ||
            isNaN(stopAnswers) ||
            stopScoreboard > stopAnswers ||
            stopAnswers > duration;

        if (invalid) {
            showFallback();
            return;
        }

        var pLive = (stopScoreboard / duration) * 100;
        var pFrozen = ((stopAnswers - stopScoreboard) / duration) * 100;
        var pSilence = ((duration - stopAnswers) / duration) * 100;

        // Correct floating-point rounding by adjusting the largest segment
        var diff = 100 - (pLive + pFrozen + pSilence);
        if (Math.abs(diff) > 1e-9) {
            if (pLive >= pFrozen && pLive >= pSilence) {
                pLive += diff;
            } else if (pFrozen >= pSilence) {
                pFrozen += diff;
            } else {
                pSilence += diff;
            }
        }

        showPhases(pLive, pFrozen, pSilence);
    }

    document.addEventListener('DOMContentLoaded', function () {
        update();

        ['duration_minutes', 'stop_updating_scoreboard', 'stop_answers_after'].forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.addEventListener('input', update);
        });
    });
})();
