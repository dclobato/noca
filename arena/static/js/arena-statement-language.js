// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Statement-language confirmation for the Arena problem form.
 *
 * When the author explicitly picks a language, this script asks the detection
 * endpoint whether the statement agrees before letting the form submit, so a
 * disagreement is settled without a server round-trip that would drop pending
 * file uploads. It also re-opens the same modal when the server itself refused
 * the save (it renders the conflict as data attributes on the select).
 *
 * The server runs the same check on every POST and remains authoritative: any
 * failure here simply lets the form submit normally.
 */
(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    var form = document.getElementById('edit-form');
    var select = document.getElementById('statement_language');
    var confirmedInput = document.getElementById('language-confirmed');
    var modalEl = document.getElementById('statement-language-modal');
    if (!form || !select || !confirmedInput || !modalEl || !window.bootstrap) return;

    var statementEl = document.getElementById('stmt-md-editor');
    var titleEl = document.getElementById('title');
    var detectUrl = select.getAttribute('data-detect-language-url') || '';
    var labels = parseLabels(modalEl.getAttribute('data-language-labels'));
    var modal = new window.bootstrap.Modal(modalEl);
    var statementText = statementEl ? statementEl.value : '';
    var submitting = false;

    // EasyMDE only writes back into the textarea on submit, so track its live
    // value through the shared editor's change notification.
    document.addEventListener('noca:problem-statement-changed', function (event) {
      statementText = (event.detail && event.detail.value) || '';
      clearConfirmation();
    });
    select.addEventListener('change', clearConfirmation);
    if (titleEl) titleEl.addEventListener('input', clearConfirmation);

    form.addEventListener('submit', function (event) {
      if (submitting || !select.value || !detectUrl) return;
      if (confirmedInput.value.indexOf(select.value + ':') === 0) return;
      event.preventDefault();
      requestDetection()
        .then(function (detected) {
          if (!detected || detected === select.value) {
            resubmit(select.value + ':' + (detected || select.value));
            return;
          }
          openModal(select.value, detected);
        })
        .catch(function () {
          // The helper endpoint is unavailable: let the authoritative
          // server-side check decide instead of blocking the save.
          resubmit('');
        });
    });

    modalEl.querySelector('[data-language-keep]').addEventListener('click', function () {
      var chosen = modalEl.getAttribute('data-language-chosen');
      var detected = modalEl.getAttribute('data-language-detected');
      select.value = chosen;
      modal.hide();
      resubmit(chosen + ':' + detected);
    });

    modalEl.querySelector('[data-language-use-detected]').addEventListener('click', function () {
      var detected = modalEl.getAttribute('data-language-detected');
      select.value = detected;
      modal.hide();
      resubmit(detected + ':' + detected);
    });

    // A conflict the server refused: re-open the modal over the re-rendered form.
    var serverChosen = select.getAttribute('data-language-conflict-chosen');
    var serverDetected = select.getAttribute('data-language-conflict-detected');
    if (serverChosen && serverDetected) openModal(serverChosen, serverDetected);

    function clearConfirmation() {
      confirmedInput.value = '';
    }

    function requestDetection() {
      return fetch(detectUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          statement: statementText,
          title: titleEl ? titleEl.value : ''
        })
      }).then(function (response) {
        if (!response.ok) throw new Error('detection failed');
        return response.json();
      }).then(function (data) {
        return data && data.language ? data.language : '';
      });
    }

    function openModal(chosen, detected) {
      modalEl.setAttribute('data-language-chosen', chosen);
      modalEl.setAttribute('data-language-detected', detected);
      setText('[data-language-chosen-label]', labels[chosen] || chosen);
      setText('[data-language-detected-label]', labels[detected] || detected);
      modal.show();
    }

    function setText(selector, text) {
      var nodes = modalEl.querySelectorAll(selector);
      for (var i = 0; i < nodes.length; i += 1) nodes[i].textContent = text;
    }

    function resubmit(token) {
      confirmedInput.value = token;
      submitting = true;
      if (form.requestSubmit) {
        form.requestSubmit();
      } else {
        form.submit();
      }
    }

    function parseLabels(raw) {
      var map = {};
      var pairs = (raw || '').split(',');
      for (var i = 0; i < pairs.length; i += 1) {
        var parts = pairs[i].split('=');
        if (parts.length === 2) map[parts[0]] = parts[1];
      }
      return map;
    }
  });
})();
