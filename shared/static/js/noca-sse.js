//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Shared EventSource wrapper for every plain SSE consumer in Web and Arena.
//
// The browser's own EventSource reconnects after a *network* failure but not
// after an HTTP refusal: a non-200 answer -- the `429` the connection lease
// returns when a client IP or user already holds its quota of streams --
// fails the connection permanently (`readyState === CLOSED`) and nothing is
// retried. Left alone, that is a silent failure: the page simply never sees
// another verdict. This wrapper turns it into a visible one. When a source
// closes that way it shows a single page-level notice, keeps retrying with a
// capped backoff, and clears the notice the moment a stream opens again.
//
// Usage:
//   stream = NocaSse.open(url, {
//     onMessage: function (event) { ... },   // required
//     onOpen: function () { ... },           // optional
//     onUnavailable: function () { ... },    // optional, once per outage
//     onRecovered: function () { ... },      // optional, once per recovery
//   });
//   stream.close();
//
// Network errors are left to the browser exactly as before; only the
// permanent closure is handled here.
var NocaSse = (function () {
  'use strict';

  var BANNER_ID = 'noca-sse-banner';
  var BANNER_TEXT =
    'Live updates are unavailable right now (too many open connections). ' +
    'This page keeps retrying; reload it to see new results meanwhile.';
  var RETRY_STEPS_MS = [5000, 10000, 20000, 40000, 60000];

  var unavailableStreams = 0;

  function banner() {
    return document.getElementById(BANNER_ID);
  }

  function showBanner() {
    if (banner() || !document.body) return;
    var el = document.createElement('div');
    el.id = BANNER_ID;
    el.className = 'noca-sse-banner alert alert-warning shadow-sm';
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.textContent = BANNER_TEXT;
    document.body.appendChild(el);
  }

  function hideBanner() {
    var el = banner();
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  function markUnavailable() {
    unavailableStreams += 1;
    showBanner();
  }

  function markRecovered() {
    unavailableStreams = Math.max(0, unavailableStreams - 1);
    if (unavailableStreams === 0) hideBanner();
  }

  function isClosed(source) {
    var Ctor = window.EventSource;
    return !!Ctor && Ctor.CLOSED !== undefined && source.readyState === Ctor.CLOSED;
  }

  function open(url, handlers) {
    handlers = handlers || {};
    var source = null;
    var retryTimer = null;
    var attempt = 0;
    var unavailable = false;
    var closed = false;

    function connect() {
      source = new window.EventSource(url);
      source.onmessage = function (event) {
        if (handlers.onMessage) handlers.onMessage(event);
      };
      source.onopen = function () {
        attempt = 0;
        if (unavailable) {
          unavailable = false;
          markRecovered();
          if (handlers.onRecovered) handlers.onRecovered();
        }
        if (handlers.onOpen) handlers.onOpen();
      };
      source.onerror = function () {
        if (closed || !isClosed(source)) return; // transient: the browser retries
        if (!unavailable) {
          unavailable = true;
          markUnavailable();
          if (handlers.onUnavailable) handlers.onUnavailable();
        }
        var delay = RETRY_STEPS_MS[Math.min(attempt, RETRY_STEPS_MS.length - 1)];
        attempt += 1;
        retryTimer = window.setTimeout(function () {
          retryTimer = null;
          if (!closed) connect();
        }, delay);
      };
    }

    function close() {
      closed = true;
      if (retryTimer !== null) {
        window.clearTimeout(retryTimer);
        retryTimer = null;
      }
      if (source) source.close();
      if (unavailable) {
        unavailable = false;
        markRecovered();
      }
    }

    connect();
    return { close: close };
  }

  return { open: open, RETRY_STEPS_MS: RETRY_STEPS_MS, BANNER_ID: BANNER_ID };
})();
