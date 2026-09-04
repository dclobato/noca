// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Browser drafts for long forms, so a Save that bounces off an expired session
 * never loses the author's work.
 *
 * Declarative contract (see docs/SHARED_SERVICES.md):
 *   <form data-noca-draft="KEY">          opt a form in; KEY is stable per form
 *   <form data-noca-draft-clear>          logout: remove every NOCA draft first
 *   data-noca-draft-ignore                on a control: never persisted
 *   [data-noca-draft-owner="TOKEN"]       rendered by _base.html for a live session
 *   [data-noca-draft-confirmed="K1 K2"]   rendered after a save committed
 *   [data-noca-draft-slot="FORM_ID"]      optional host for the notices
 *   [data-noca-presence][data-heartbeat-url]  reused as the pre-submit auth probe
 *
 * Drafts are keyed `noca:form-draft:{owner}:{KEY}` and are only ever offered to
 * the account that wrote them. Nothing is cleared on the browser's submit
 * event -- that is exactly the Save that then bounces -- so only the server's
 * confirmation, an explicit Discard, or a logout removes a draft.
 */
(function () {
  'use strict';

  var KEY_PREFIX = 'noca:form-draft:';
  var DEBOUNCE_MS = 1000;
  var BYPASS_RESET_MS = 5000;
  var COLLECT_EVENT = 'noca:form-draft-collect';
  var RESTORED_EVENT = 'noca:form-draft-restored';

  function storageGet(key) {
    try { return window.localStorage.getItem(key); } catch (_error) { return null; }
  }

  function storageRemove(key) {
    try { window.localStorage.removeItem(key); } catch (_error) { /* nothing to lose */ }
  }

  function draftKeys() {
    var keys = [];
    try {
      var store = window.localStorage;
      for (var i = 0; i < store.length; i += 1) {
        var key = store.key(i);
        if (key && key.indexOf(KEY_PREFIX) === 0) keys.push(key);
      }
    } catch (_error) { /* storage unavailable: nothing stored */ }
    return keys;
  }

  function clearAll() {
    draftKeys().forEach(storageRemove);
  }

  function purgeOtherOwners(owner) {
    var mine = KEY_PREFIX + owner + ':';
    draftKeys().forEach(function (key) {
      if (key.indexOf(mine) !== 0) storageRemove(key);
    });
  }

  function storageKey(owner, formKey) {
    return KEY_PREFIX + owner + ':' + formKey;
  }

  function isPersisted(control) {
    if (!control.name || control.disabled) return false;
    if (control.hasAttribute('data-noca-draft-ignore')) return false;
    var type = (control.type || '').toLowerCase();
    if (type === 'file' || type === 'submit' || type === 'button' || type === 'reset') return false;
    if (type === 'image' || type === 'fieldset' || type === 'output') return false;
    return true;
  }

  // Ordered [name, value] pairs, exactly what the form would submit minus files.
  function serialize(form) {
    var fields = [];
    Array.prototype.forEach.call(form.elements, function (control) {
      if (!isPersisted(control)) return;
      var type = (control.type || '').toLowerCase();
      if (type === 'checkbox' || type === 'radio') {
        if (control.checked) fields.push([control.name, control.value]);
      } else if (type === 'select-multiple') {
        Array.prototype.forEach.call(control.options, function (option) {
          if (option.selected) fields.push([control.name, option.value]);
        });
      } else {
        fields.push([control.name, control.value]);
      }
    });
    return fields;
  }

  function sameFields(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
  }

  function dispatchChanged(control) {
    control.dispatchEvent(new Event('input', { bubbles: true }));
    control.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function restoreFields(form, fields) {
    var byName = {};
    fields.forEach(function (pair) {
      (byName[pair[0]] = byName[pair[0]] || []).push(pair[1]);
    });
    var cursor = {};
    Array.prototype.forEach.call(form.elements, function (control) {
      if (!isPersisted(control)) return;
      var known = Object.prototype.hasOwnProperty.call(byName, control.name);
      var type = (control.type || '').toLowerCase();
      var checkable = type === 'checkbox' || type === 'radio';
      // A box that was unchecked at save time simply has no entry.
      if (!known && !checkable) return;
      var values = known ? byName[control.name] : [];
      var changed = false;
      if (checkable) {
        var checked = values.indexOf(control.value) !== -1;
        changed = control.checked !== checked;
        control.checked = checked;
      } else if (type === 'select-multiple') {
        Array.prototype.forEach.call(control.options, function (option) {
          var selected = values.indexOf(option.value) !== -1;
          if (option.selected !== selected) changed = true;
          option.selected = selected;
        });
      } else {
        var index = cursor[control.name] || 0;
        if (index < values.length) {
          cursor[control.name] = index + 1;
          changed = control.value !== values[index];
          control.value = values[index];
        }
      }
      if (changed) dispatchChanged(control);
    });
  }

  function noticeHost(form) {
    return form.id ? document.querySelector('[data-noca-draft-slot="' + form.id + '"]') : null;
  }

  function showNotice(form, variant, message, actions) {
    var previous = form.nocaDraftNotice;
    if (previous && previous.parentNode) previous.parentNode.removeChild(previous);
    var el = document.createElement('div');
    el.className = 'noca-form-draft-notice alert ' + variant;
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    var text = document.createElement('div');
    text.textContent = message;
    el.appendChild(text);
    if (actions && actions.length) {
      var bar = document.createElement('div');
      bar.className = 'noca-form-draft-actions';
      actions.forEach(function (action) {
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn btn-sm ' + action.className;
        button.textContent = action.label;
        button.addEventListener('click', function () {
          action.onClick();
          if (el.parentNode) el.parentNode.removeChild(el);
        });
        bar.appendChild(button);
      });
      el.appendChild(bar);
    }
    var host = noticeHost(form);
    if (host) host.appendChild(el); else form.insertAdjacentElement('beforebegin', el);
    form.nocaDraftNotice = el;
    return el;
  }

  function hideNotice(form) {
    var el = form.nocaDraftNotice;
    if (el && el.parentNode) el.parentNode.removeChild(el);
    form.nocaDraftNotice = null;
  }

  function probeUrl() {
    var config = document.querySelector('[data-noca-presence]');
    return config && config.dataset.heartbeatUrl ? config.dataset.heartbeatUrl : '';
  }

  function bindForm(form, owner) {
    var key = storageKey(owner, form.getAttribute('data-noca-draft'));
    var timer = null;
    var warned = false;
    var bypass = false;
    var probing = false;
    var lastSubmitter = null;

    function collectMeta() {
      var detail = { meta: {} };
      form.dispatchEvent(new CustomEvent(COLLECT_EVENT, { bubbles: true, detail: detail }));
      return detail.meta;
    }

    function write() {
      timer = null;
      var payload = { v: 1, savedAt: new Date().toISOString(), fields: serialize(form), meta: collectMeta() };
      try {
        window.localStorage.setItem(key, JSON.stringify(payload));
      } catch (_error) {
        if (warned) return;
        warned = true;
        showNotice(form, 'alert-warning',
          'This browser could not keep a local draft of this form (storage unavailable or full). ' +
          'Your work is only safe once it is saved.');
      }
    }

    function schedule() {
      if (timer !== null) window.clearTimeout(timer);
      timer = window.setTimeout(write, DEBOUNCE_MS);
    }

    function flush() {
      if (timer === null) return;
      window.clearTimeout(timer);
      write();
    }

    // A Save is the one moment the draft must be current whether or not a
    // debounced write is pending: it is the copy that survives a bounce.
    function flushNow() {
      if (timer !== null) window.clearTimeout(timer);
      write();
    }

    function readDraft() {
      var raw = storageGet(key);
      if (raw === null) return null;
      try {
        var draft = JSON.parse(raw);
        if (draft && draft.v === 1 && Array.isArray(draft.fields)) return draft;
      } catch (_error) { /* fall through */ }
      storageRemove(key);
      return null;
    }

    function offerRestore(draft) {
      var when = new Date(draft.savedAt);
      var stamp = isNaN(when.getTime()) ? 'an earlier session' : when.toLocaleString();
      showNotice(form, 'alert-warning',
        'An unsaved draft of this form from ' + stamp + ' was found in this browser. ' +
        'Restore it to continue where you left off. Files (an illustration or a statement PDF) ' +
        'are not part of a draft and must be chosen again; drafts live in this browser only.',
        [
          { label: 'Restore draft', className: 'btn-primary', onClick: function () {
            restoreFields(form, draft.fields);
            form.dispatchEvent(new CustomEvent(RESTORED_EVENT, {
              bubbles: true, detail: { fields: draft.fields, meta: draft.meta || {} }
            }));
            schedule();
          } },
          { label: 'Discard draft', className: 'btn-outline-secondary', onClick: function () {
            storageRemove(key);
          } },
          { label: 'Not now', className: 'btn-link', onClick: function () {} }
        ]);
    }

    function resubmit() {
      bypass = true;
      window.setTimeout(function () { bypass = false; }, BYPASS_RESET_MS);
      if (typeof form.requestSubmit === 'function') form.requestSubmit(lastSubmitter || undefined);
      else form.submit();
    }

    function probe(url) {
      probing = true;
      var request;
      try {
        request = window.fetch(url, {
          method: 'POST',
          headers: { Accept: 'application/json' },
          credentials: 'same-origin',
          redirect: 'manual'
        });
      } catch (error) {
        request = Promise.reject(error);
      }
      request.then(function (response) {
        probing = false;
        if (response.ok) { resubmit(); return; }
        var dead = response.type === 'opaqueredirect' || response.status === 401 || response.status === 403;
        if (dead) {
          showNotice(form, 'alert-danger',
            'Your session has expired, so this Save was not sent. Your draft is kept in this ' +
            'browser: sign in again in another tab, then come back and Save.');
          return;
        }
        unverified();
      }, function () {
        probing = false;
        unverified();
      });
    }

    function unverified() {
      showNotice(form, 'alert-warning',
        'Your session could not be verified (network or server problem). Your draft is kept ' +
        'in this browser. You can wait and try again, or save anyway.',
        [{ label: 'Save anyway', className: 'btn-outline-primary', onClick: resubmit }]);
    }

    document.addEventListener('input', function (event) {
      if (event.target && event.target.form === form) schedule();
    });
    document.addEventListener('change', function (event) {
      if (event.target && event.target.form === form) schedule();
    });
    window.addEventListener('pagehide', flush);
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'hidden') flush();
    });

    // Bubbling on the document, so the page's own form listeners (validation,
    // editor sync) have already run and may have cancelled the submit.
    document.addEventListener('submit', function (event) {
      if (event.target !== form || event.defaultPrevented) return;
      flushNow();
      if (bypass) { bypass = false; return; }
      var url = probeUrl();
      if (!url) return;
      event.preventDefault();
      if (probing) return;
      lastSubmitter = event.submitter || null;
      hideNotice(form);
      probe(url);
    });

    var draft = readDraft();
    if (draft && !sameFields(draft.fields, serialize(form))) offerRestore(draft);
  }

  function bindLogout(form) {
    form.addEventListener('submit', clearAll);
  }

  function init() {
    var ownerEl = document.querySelector('[data-noca-draft-owner]');
    var owner = ownerEl ? ownerEl.getAttribute('data-noca-draft-owner') : '';
    var confirmedEl = document.querySelector('[data-noca-draft-confirmed]');
    if (owner) {
      purgeOtherOwners(owner);
      if (confirmedEl) {
        (confirmedEl.getAttribute('data-noca-draft-confirmed') || '').split(/\s+/).forEach(function (formKey) {
          if (formKey) storageRemove(storageKey(owner, formKey));
        });
      }
      Array.prototype.forEach.call(document.querySelectorAll('form[data-noca-draft]'), function (form) {
        bindForm(form, owner);
      });
    }
    Array.prototype.forEach.call(document.querySelectorAll('form[data-noca-draft-clear]'), bindLogout);
  }

  window.NocaFormDraft = Object.freeze({
    KEY_PREFIX: KEY_PREFIX,
    serialize: serialize,
    clearAll: clearAll,
    storageKey: storageKey
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
