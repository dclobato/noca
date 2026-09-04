// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Back off the timer-driven htmx partials when the server answers 429.
//
// Web and Arena put a loose per-actor ceiling on their polled read routes. A
// client that meets it is, almost by definition, one with too many timers
// running -- several stale tabs, a reopened laptop, a page left on a projector
// -- and the worst thing it can do is keep the same timer running into the
// refusal. So one 429 from a *polling* request parks every poll on the page
// until the server's own `Retry-After` has passed.
//
// Only polls are parked. A click, a form submit, or any other user-initiated
// request goes through untouched: silently dropping something a person asked
// for would look like the page is broken, while a paused background refresh
// costs them one stale minute. Polling requests are recognised by the `every`
// clause in their element's `hx-trigger`, which is exactly the set of requests
// nobody is waiting on.
//
// Both modules load this from their `_base.html`. Loading a second copy would
// double the listeners, which is harmless but pointless.

(() => {
  "use strict";

  const DEFAULT_BACKOFF_SECONDS = 60;
  const MIN_BACKOFF_SECONDS = 5;
  const MAX_BACKOFF_SECONDS = 600;

  let pausedUntil = 0;

  const isPoll = (element) => {
    if (!element || typeof element.getAttribute !== "function") return false;
    const trigger = element.getAttribute("hx-trigger") || "";
    return /(^|[\s,])every\s/.test(trigger);
  };

  const backoffSeconds = (xhr) => {
    const header = xhr && typeof xhr.getResponseHeader === "function" ? xhr.getResponseHeader("Retry-After") : null;
    const parsed = Number.parseInt(header || "", 10);
    if (!Number.isFinite(parsed) || parsed <= 0) return DEFAULT_BACKOFF_SECONDS;
    return Math.min(MAX_BACKOFF_SECONDS, Math.max(MIN_BACKOFF_SECONDS, parsed));
  };

  document.addEventListener("htmx:beforeRequest", (event) => {
    if (Date.now() >= pausedUntil) return;
    if (!isPoll(event.detail && event.detail.elt)) return;
    event.preventDefault();
  });

  document.addEventListener("htmx:responseError", (event) => {
    const detail = event.detail || {};
    if (!detail.xhr || detail.xhr.status !== 429) return;
    if (!isPoll(detail.elt)) return;
    const seconds = backoffSeconds(detail.xhr);
    pausedUntil = Math.max(pausedUntil, Date.now() + seconds * 1000);
    document.dispatchEvent(new CustomEvent("noca:poll-backoff", { detail: { seconds } }));
  });
})();
