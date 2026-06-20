// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

(function () {
  'use strict';

  var message = 'You have unsaved problem changes. Save changes before leaving this page?';
  var editForm = document.getElementById('edit-form');
  if (!editForm) return;

  var initialState = null;
  var isSubmittingEditForm = false;
  var isLeaving = false;
  var statementValue = null;

  function formControlValue(control) {
    if (control.disabled || !control.name || control.id === 'active-tab-input') return null;
    if (control.type === 'file') {
      return Array.from(control.files || []).map(function (file) {
        return file.name + ':' + file.size + ':' + file.lastModified;
      }).join('|');
    }
    if (control.type === 'checkbox' || control.type === 'radio') return control.checked;
    return control.value;
  }

  function currentStatementValue() {
    if (statementValue !== null) return statementValue;
    var statement = document.getElementById('stmt-md-editor');
    return statement ? statement.value : '';
  }

  function currentState() {
    var values = [];
    Array.from(editForm.elements).forEach(function (control) {
      var value = formControlValue(control);
      if (value !== null) values.push([control.name || control.id, value]);
    });
    values.push(['__statement', currentStatementValue()]);
    return JSON.stringify(values);
  }

  function isDirty() {
    return initialState !== null && currentState() !== initialState;
  }

  function shouldIgnoreLink(link) {
    if (!link || link.closest('[aria-disabled="true"], .disabled')) return true;
    if (link.dataset.bsToggle || link.hasAttribute('download')) return true;
    if (link.target && link.target !== '_self') return true;

    var href = link.getAttribute('href');
    return !href || href.charAt(0) === '#';
  }

  function confirmNavigation() {
    if (!isDirty()) return true;
    return window.confirm(message);
  }

  document.addEventListener('noca:problem-statement-changed', function (event) {
    statementValue = event.detail && typeof event.detail.value === 'string'
      ? event.detail.value
      : '';
  });

  document.addEventListener('noca:problem-edit-changed', function () {
    window.setTimeout(isDirty, 0);
  });

  editForm.addEventListener('input', isDirty);
  editForm.addEventListener('change', isDirty);

  editForm.addEventListener('submit', function (event) {
    if (event.defaultPrevented) return;
    isSubmittingEditForm = true;
  });

  document.addEventListener('submit', function (event) {
    if (event.target === editForm) return;
    if (!confirmNavigation()) {
      event.preventDefault();
      return;
    }
    isLeaving = true;
  }, true);

  document.addEventListener('click', function (event) {
    if (!(event.target instanceof Element)) return;
    var link = event.target.closest('a[href]');
    if (shouldIgnoreLink(link)) return;
    if (!confirmNavigation()) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    isLeaving = true;
  }, true);

  window.addEventListener('beforeunload', function (event) {
    if (isSubmittingEditForm || isLeaving || !isDirty()) return;
    event.preventDefault();
    event.returnValue = '';
  });

  document.addEventListener('DOMContentLoaded', function () {
    initialState = currentState();
  });
})();
