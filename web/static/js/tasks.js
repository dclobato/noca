// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * tasks.js — Task management page client-side logic.
 *
 * Responsibilities:
 *  1. Auto-open the task detail modal when ?open={task_id} is present in the URL
 *     (set by the acquire route on success).
 *  2. Populate the task detail modal body and footer from data attributes on the
 *     triggering button when the modal is shown.
 *  3. Pause the HTMX 60-second auto-refresh while the modal is open and resume
 *     it when the modal is closed.
 */
(function () {
  'use strict';

  // ── HTML escaping helper ──────────────────────────────────────────────
  function _esc(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function materialIcon(icon, classes, filled) {
    var className = 'material-symbols-outlined';

    if (filled) {
      className += ' material-symbols-filled';
    }

    if (classes) {
      className += ' ' + classes;
    }

    return '<span class="' + className + '">' + _esc(icon) + '</span>';
  }

  // ── HTMX auto-refresh pause / resume ─────────────────────────────────
  var wrapper = document.getElementById('tasks-list-wrapper');
  var savedTrigger = null;

  function pauseRefresh() {
    if (!wrapper) return;
    var trigger = wrapper.getAttribute('hx-trigger');
    if (trigger) {
      savedTrigger = trigger;
      wrapper.removeAttribute('hx-trigger');
    }
  }

  function resumeRefresh() {
    if (!wrapper || !savedTrigger) return;
    wrapper.setAttribute('hx-trigger', savedTrigger);
    savedTrigger = null;
    if (window.htmx) {
      htmx.process(wrapper);
    }
  }

  // ── Modal population ──────────────────────────────────────────────────
  var modal = document.getElementById('taskDetailModal');
  if (modal) {
    modal.addEventListener('show.bs.modal', function (event) {
      var btn = event.relatedTarget;
      if (!btn) return;

      pauseRefresh();

      var taskType     = btn.getAttribute('data-task-type')     || '';
      var taskFinished = btn.getAttribute('data-task-finished') === 'true';
      var teamName     = btn.getAttribute('data-team-name')     || '?';
      var teamLocation = btn.getAttribute('data-team-location') || '';
      var problemLabel = btn.getAttribute('data-problem-label') || '';
      var problemTitle = btn.getAttribute('data-problem-title') || '';
      var problemColor = btn.getAttribute('data-problem-color') || '';
      var sourceUrl    = btn.getAttribute('data-source-url')    || '';
      var finishUrl    = btn.getAttribute('data-finish-url')    || '';
      var releaseUrl   = btn.getAttribute('data-release-url')   || '';

      var body   = document.getElementById('task-modal-body');
      var footer = document.getElementById('task-modal-footer');
      if (!body || !footer) return;

      // Build body content per task type
      var bodyHtml = '';

      var locationHtml = teamLocation
        ? '<div class="text-muted small mt-1">' + _esc(teamLocation) + '</div>'
        : '';

      if (taskType === 'SOS') {
        bodyHtml =
          '<div class="mb-1"><span class="fw-semibold text-muted small">Team</span></div>' +
          '<div class="fs-5">' + _esc(teamName) + '</div>' +
          locationHtml +
          '<div class="mt-3 alert alert-danger d-flex align-items-center gap-2 mb-0">' +
          materialIcon('dangerous', 'fs-4 flex-shrink-0', true) +
          '<span>SOS — This team needs immediate assistance.</span>' +
          '</div>';

      } else if (taskType === 'PRINT') {
        bodyHtml =
          '<div class="mb-1"><span class="fw-semibold text-muted small">Team</span></div>' +
          '<div>' + _esc(teamName) + '</div>' +
          locationHtml +
          '<div class="mt-3 mb-1"><span class="fw-semibold text-muted small">Source code</span></div>' +
          '<a href="' + _esc(sourceUrl) + '" class="btn btn-outline-secondary btn-sm" target="_blank">' +
          materialIcon('download', 'me-1') + 'Download source</a>';

      } else if (taskType === 'BALLOON' || taskType === 'FIRST_BALLOON') {
        // Strip leading '#' from hex color for the asset URL
        var colorHex = problemColor.replace(/^#/, '');
        var assetKind = taskType === 'FIRST_BALLOON' ? 'star' : 'balloon';
        var assetAlt = taskType === 'FIRST_BALLOON' ? 'star balloon' : 'balloon';
        var balloonClass = taskType === 'FIRST_BALLOON' ? ' class="noca-first-balloon-lg"' : '';
        var problemDisplay = problemLabel
          ? (_esc(problemLabel) + (problemTitle ? ': ' + _esc(problemTitle) : ''))
          : '—';
        bodyHtml =
          '<div class="row align-items-center g-3">' +
          '<div class="col-md-5 text-center">' +
          '<img src="/assets/' + assetKind + '/' + _esc(colorHex) + '" width="180" height="270"' + balloonClass + ' alt="' + assetAlt + '">' +
          '</div>' +
          '<div class="col-md-7">' +
          '<div class="mb-1"><span class="fw-semibold text-muted small">Team</span></div>' +
          '<div class="fs-5">' + _esc(teamName) + '</div>' +
          locationHtml +
          '<div class="mt-3 mb-1"><span class="fw-semibold text-muted small">Problem</span></div>' +
          '<div class="fw-semibold fs-5">' + problemDisplay + '</div>' +
          '</div>' +
          '</div>';

      } else {
        bodyHtml = '<p class="text-muted">Unknown task type.</p>';
      }

      body.innerHTML = bodyHtml;

      // Build footer
      if (taskFinished) {
        footer.innerHTML =
          '<button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal">Close</button>';
      } else {
        var footerHtml =
          '<button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal">Close</button>';
        if (finishUrl) {
          footerHtml +=
            '<form method="post" action="' + _esc(finishUrl) + '" class="d-inline ms-1">' +
            '<button type="submit" class="btn btn-success btn-sm">' +
            materialIcon('check', 'me-1') + 'Finish Task</button></form>';
        }
        if (releaseUrl) {
          footerHtml +=
            '<form method="post" action="' + _esc(releaseUrl) + '" class="d-inline ms-1">' +
            '<button type="submit" class="btn btn-outline-warning btn-sm">' +
            materialIcon('lock_open', 'me-1') + 'Release Task</button></form>';
        }
        footer.innerHTML = footerHtml;
      }
    });

    modal.addEventListener('hidden.bs.modal', function () {
      resumeRefresh();
    });
  }

  // ── Auto-open from ?open= URL param ──────────────────────────────────
  function autoOpenFromParam() {
    var params = new URLSearchParams(window.location.search);
    var openId = params.get('open');
    if (!openId) return;

    // Clean the URL before opening the modal so a page refresh won't re-open it
    var url = new URL(window.location.href);
    url.searchParams.delete('open');
    window.history.replaceState({}, '', url.toString());

    // Find the Open button for this task and simulate a click to trigger the modal
    var btn = document.querySelector('[data-task-id="' + openId + '"][data-bs-toggle="modal"]');
    if (btn) {
      btn.click();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', autoOpenFromParam);
  } else {
    autoOpenFromParam();
  }

})();
