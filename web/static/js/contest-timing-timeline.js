// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

(function () {
    'use strict';

    var SEG_FALLBACK = document.getElementById('timeline-seg-fallback');
    var SEG_LIVE = document.getElementById('timeline-seg-live');
    var SEG_FROZEN = document.getElementById('timeline-seg-frozen');
    var SEG_SILENCE = document.getElementById('timeline-seg-silence');

    if (!SEG_FALLBACK || !SEG_LIVE || !SEG_FROZEN || !SEG_SILENCE) return;

    function getInt(id) {
        var el = document.getElementById(id);
        if (!el) return NaN;
        var v = parseInt(el.value, 10);
        return isNaN(v) || v <= 0 ? NaN : v;
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
        var duration = getInt('duration_minutes');
        var stopScoreboard = getInt('stop_updating_scoreboard');
        var stopAnswers = getInt('stop_answers_after');

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
