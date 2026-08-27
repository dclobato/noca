/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/*
 * Presence and session-keepalive client. Two jobs on the same interval:
 *   1. Heartbeat: POST to keep the current user marked online. This request is
 *      also what keeps the sliding auth session alive while the user reads or
 *      types without navigating, so it runs unconditionally -- a page open for
 *      an hour must not lose its session, and an unsaved form with it.
 *   2. Dots: collect every `.avatar-wrapper[data-user-id]` on the page, ask the
 *      status endpoint which are online, and toggle the `online` class. This
 *      half is the green-dot feature proper and is skipped when
 *      `data-presence-enabled="false"` or when no status endpoint is configured.
 *
 * Module-neutral: endpoint URLs and the interval come from a config element
 * (`[data-noca-presence]`), so a module reuses this file by pointing it at its
 * own routes. Arena configures both jobs; web configures the heartbeat alone,
 * having no presence domain -- which is why only `data-heartbeat-url` is
 * required. Fails silently when offline or when config is absent.
 */
(() => {
  "use strict";

  const config = document.querySelector("[data-noca-presence]");
  if (!config) {
    return;
  }

  const heartbeatUrl = config.dataset.heartbeatUrl;
  // Optional: a consumer that only needs the keepalive (the web module) has no
  // status endpoint to point at, and must still heartbeat.
  const statusUrl = config.dataset.statusUrl;
  if (!heartbeatUrl) {
    return;
  }

  const intervalSeconds = Number(config.dataset.intervalSeconds) || 30;
  const intervalMs = Math.max(5, intervalSeconds) * 1000;
  // Absent means enabled, so a consumer that predates this attribute (or one
  // that never disables presence) keeps its dots.
  const presenceEnabled = config.dataset.presenceEnabled !== "false";

  const collectIds = () => {
    const ids = new Set();
    document.querySelectorAll(".avatar-wrapper[data-user-id]").forEach((el) => {
      const id = el.dataset.userId;
      if (id) {
        ids.add(id);
      }
    });
    return [...ids];
  };

  const applyOnline = (onlineIds) => {
    const onlineSet = new Set(onlineIds);
    document.querySelectorAll(".avatar-wrapper[data-user-id]").forEach((el) => {
      el.classList.toggle("online", onlineSet.has(el.dataset.userId));
    });
  };

  const sendHeartbeat = async () => {
    try {
      await fetch(heartbeatUrl, {
        method: "POST",
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        keepalive: true,
      });
    } catch (_error) {
      /* offline: ignore, retried next tick */
    }
  };

  const refreshDots = async () => {
    const ids = collectIds();
    if (!ids.length) {
      return;
    }
    try {
      const response = await fetch(statusUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ ids }),
      });
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      if (payload && Array.isArray(payload.online)) {
        applyOnline(payload.online);
      }
    } catch (_error) {
      /* offline: ignore, retried next tick */
    }
  };

  const tick = () => {
    void sendHeartbeat();
    if (presenceEnabled && statusUrl) {
      void refreshDots();
    }
  };

  tick();
  setInterval(tick, intervalMs);
})();
