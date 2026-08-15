// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Tab-state controller for the problem editor, shared by the Web and Arena
// admin editors.
//
// Every pane stays mounted and every control attaches to the detached
// `#edit-form`, so Bootstrap only toggles visibility here -- switching tabs can
// never lose typed input. This script owns the two things Bootstrap does not:
// remembering which tab was open across a save or a redirect (through the hidden
// `active_tab` field the server round-trips), and honouring an incoming `?tab=`.
//
// The server has already resolved `?tab=` into `data-active-tab` and rendered
// that pane active, so the client only re-applies it for a same-document
// navigation, e.g. following a `#tc-42` link that also carries `?tab=test-cases`.
// An unknown value is ignored rather than treated as an error: a tab is a view
// preference, not a correctness decision.
//
// Tab switching deliberately does not count as an unsaved change --
// problem-edit-unsaved-guard.js skips `#active-tab-input` by id.

(function () {
  'use strict';

  var tabList = document.getElementById('problem-edit-tabs');
  if (!tabList) return;

  var activeTabInput = document.getElementById('active-tab-input');

  function buttonForTab(tab) {
    if (!tab) return null;
    return tabList.querySelector('[data-tab-value="' + CSS.escape(tab) + '"]');
  }

  function scrollTabIntoView(button) {
    // Five tabs do not fit a phone, so the strip scrolls horizontally. Keep the
    // active tab visible without scrolling the page itself.
    if (!button || typeof button.scrollIntoView !== 'function') return;
    button.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  function requestedTab() {
    try {
      return new URL(window.location.href).searchParams.get('tab');
    } catch (err) {
      return null;
    }
  }

  // Record the open tab so a save, a validation failure, or a redirect comes
  // back to where the author was working.
  tabList.addEventListener('shown.bs.tab', function (event) {
    var tab = event.target ? event.target.getAttribute('data-tab-value') : null;
    if (!tab) return;
    if (activeTabInput) activeTabInput.value = tab;
    scrollTabIntoView(event.target);
  });

  var wanted = requestedTab();
  var button = buttonForTab(wanted);
  if (button && !button.classList.contains('active') && window.bootstrap && window.bootstrap.Tab) {
    window.bootstrap.Tab.getOrCreateInstance(button).show();
  } else {
    scrollTabIntoView(tabList.querySelector('.nav-link.active'));
  }
})();
