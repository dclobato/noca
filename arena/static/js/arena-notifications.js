/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

(() => {
  "use strict";

  const root = document.querySelector("[data-arena-notifications]");
  if (!root) {
    return;
  }

  const toggle = root.querySelector("[data-arena-notifications-toggle]");
  const menu = root.querySelector("[data-arena-notifications-menu]");
  const list = root.querySelector("[data-arena-notifications-list]");
  const badge = root.querySelector("[data-arena-notifications-badge]");
  const listUrl = root.dataset.arenaNotificationsListUrl || root.dataset.listUrl;

  if (!toggle || !menu || !list || !badge || !listUrl) {
    return;
  }

  const readAllUrl = `${listUrl}/read-all`;

  let loaded = false;

  const escapeHtml = (value) =>
    String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");

  const updateBadge = (count) => {
    const unreadCount = Number(count) || 0;
    badge.textContent = String(unreadCount);
    badge.classList.toggle("d-none", unreadCount <= 0);
  };

  const notificationTime = (createdAt) => {
    if (!createdAt) {
      return "";
    }
    const date = new Date(createdAt);
    if (Number.isNaN(date.getTime())) {
      return "";
    }
    return date.toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    });
  };

  const renderNotifications = (notifications) => {
    if (!notifications.length) {
      list.innerHTML = '<div class="arena-notifications-empty">No unread notifications.</div>';
      return;
    }

    const items = notifications
      .map((notification) => {
        const targetUrl = notification.target_url || "#";
        const unreadClass = notification.read_at ? "" : " is-unread";
        const timeText = notification.created_at_display || notificationTime(notification.created_at);
        const icon = escapeHtml(notification.icon || "notifications");
        return `
          <a href="${escapeHtml(targetUrl)}"
             class="arena-notification-item${unreadClass}"
             data-arena-notification-id="${escapeHtml(notification.id)}"
             data-arena-notification-read-url="${escapeHtml(`${listUrl}/${notification.id}/read`)}">
            <span class="arena-notification-title">
              <span class="material-symbols-outlined arena-notification-icon">${icon}</span>${escapeHtml(notification.title)}</span>
            <span class="arena-notification-message">${escapeHtml(notification.message)}</span>
            ${timeText ? `<span class="arena-notification-time">${escapeHtml(timeText)}</span>` : ""}
          </a>
        `;
      })
      .join("");

    const hasUnread = notifications.some((n) => !n.read_at);
    const markAllBtn = hasUnread
      ? `<div class="arena-notifications-actions">
           <button type="button" class="btn btn-link btn-sm arena-notifications-mark-all-read">Mark all as read</button>
         </div>`
      : "";

    list.innerHTML = markAllBtn + items;
  };

  const loadNotifications = async () => {
    list.innerHTML = '<div class="arena-notifications-loading">Loading...</div>';
    try {
      const response = await fetch(listUrl, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!response.ok) {
        throw new Error("Could not load notifications.");
      }
      const payload = await response.json();
      updateBadge(payload.unread_count);
      const unread = (payload.notifications || []).filter((n) => !n.read_at);
      renderNotifications(unread);
      loaded = true;
    } catch (_error) {
      list.innerHTML = '<div class="arena-notifications-error">Could not load notifications.</div>';
    }
  };

  const setOpen = (open) => {
    menu.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    if (open && !loaded) {
      void loadNotifications();
    }
  };

  toggle.addEventListener("click", () => {
    setOpen(menu.hidden);
  });

  document.addEventListener("click", (event) => {
    if (!root.contains(event.target)) {
      setOpen(false);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      setOpen(false);
    }
  });

  list.addEventListener("click", (event) => {
    const markAllBtn = event.target.closest(".arena-notifications-mark-all-read");
    if (markAllBtn) {
      markAllBtn.disabled = true;
      void fetch(readAllUrl, {
        method: "POST",
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      })
        .then((response) => (response.ok ? response.json() : null))
        .then((payload) => {
          if (payload) {
            updateBadge(payload.unread_count);
          }
          loaded = false;
          void loadNotifications();
        });
      return;
    }

    const item = event.target.closest("[data-arena-notification-id]");
    if (!item) {
      return;
    }

    event.preventDefault();

    const readUrl = item.dataset.arenaNotificationReadUrl;
    const targetHref = item.getAttribute("href");
    const hasTarget = targetHref && targetHref !== "#" && !targetHref.endsWith("#");

    if (readUrl) {
      item.classList.remove("is-unread");
      void fetch(readUrl, {
        method: "POST",
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        keepalive: true,
      });
    }

    if (hasTarget) {
      window.location.href = targetHref;
    }
  });
})();
