// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Loads the 30-day uptime JSON contract and renders one ECharts heatmap per
// service. HTMX swaps recreate the charts from fresh data; an accessible table
// exposes the same timestamps, percentages, and probe counts without canvas.
(function () {
  "use strict";

  const FLOOR_PCT = 70;
  const LIVE_REGION_ID = "healthmon-dashboard-live";
  const chartManagers = {};

  window.healthmonRefreshPaused = false;

  function cssToken(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function palette() {
    const tokens = NocaECharts.tokens();
    return {
      down: cssToken("--noca-danger"),
      mixed: cssToken("--noca-warning"),
      up: cssToken("--noca-success"),
      empty: tokens.split,
      border: tokens.sliceBorder,
    };
  }

  function formatTimestamp(value) {
    return new Date(value).toISOString().slice(0, 16).replace("T", " ") + " UTC";
  }

  function slotDetail(slot) {
    const timestamp = formatTimestamp(slot.started_at);
    if (slot.uptime_pct === null) return timestamp + " — no probe data";
    const percent = slot.uptime_pct.toFixed(1);
    return `${timestamp} — ${percent}% up (${slot.up} of ${slot.total} probes)`;
  }

  function buildOption(service) {
    const colors = palette();
    const uptimeData = [];
    const emptyData = [];
    service.slots.forEach(function (slot, index) {
      const point = {
        value: [index, 0, Math.max(FLOOR_PCT, slot.uptime_pct || 0)],
        detail: slotDetail(slot),
      };
      if (slot.uptime_pct === null) emptyData.push(point);
      else uptimeData.push(point);
    });

    const seriesStyle = {
      borderColor: colors.border,
      borderWidth: 1,
      borderRadius: 1,
    };
    return {
      animation: false,
      aria: {
        enabled: true,
        label: {
          description:
            "30-day uptime heatmap for " +
            service.title +
            ". Each cell represents 12 hours. A data table follows the chart.",
        },
        decal: { show: false },
      },
      tooltip: {
        trigger: "item",
        confine: true,
        formatter: function (params) {
          return params.data.detail;
        },
      },
      grid: { top: 0, right: 0, bottom: 0, left: 0 },
      xAxis: {
        type: "category",
        data: service.slots.map(function (_, index) {
          return index;
        }),
        show: false,
      },
      yAxis: { type: "category", data: [service.title], show: false },
      visualMap: {
        show: false,
        min: FLOOR_PCT,
        max: 100,
        dimension: 2,
        seriesIndex: 0,
        inRange: { color: [colors.down, colors.mixed, colors.up] },
      },
      series: [
        {
          name: "Uptime",
          type: "heatmap",
          data: uptimeData,
          itemStyle: seriesStyle,
          emphasis: { itemStyle: { borderColor: NocaECharts.tokens().ink, borderWidth: 2 } },
        },
        {
          name: "No probe data",
          type: "heatmap",
          data: emptyData,
          itemStyle: Object.assign({ color: colors.empty }, seriesStyle),
          emphasis: { itemStyle: { borderColor: NocaECharts.tokens().ink, borderWidth: 2 } },
        },
      ],
    };
  }

  function populateTable(container, service) {
    const card = container.closest(".healthmon-card");
    const body = card.querySelector("[data-healthmon-uptime-table]");
    body.replaceChildren();
    service.slots.forEach(function (slot) {
      const row = document.createElement("tr");
      const timestamp = document.createElement("th");
      const uptime = document.createElement("td");
      const probes = document.createElement("td");
      timestamp.scope = "row";
      timestamp.textContent = formatTimestamp(slot.started_at);
      uptime.textContent = slot.uptime_pct === null ? "No data" : slot.uptime_pct.toFixed(1) + "%";
      probes.textContent = slot.total === 0 ? "—" : slot.up + " / " + slot.total;
      row.append(timestamp, uptime, probes);
      body.append(row);
    });
  }

  function renderChart(container, service) {
    const manager = chartManagers[container.id];
    const detail = document.getElementById("heatmap-detail-" + service.key);
    const hasHistory = service.slots.some(function (slot) {
      return slot.uptime_pct !== null;
    });
    manager.hideLoading();
    manager.render(function (chart) {
      chart.setOption(buildOption(service), true);
      chart.off("click");
      chart.on("click", function (params) {
        if (params.data && params.data.detail) detail.textContent = params.data.detail;
      });
    });
    container.setAttribute("aria-busy", "false");
    container.setAttribute("aria-label", "30-day uptime heatmap for " + service.title);
    detail.textContent = hasHistory
      ? "Select a chart cell to inspect its 12-hour uptime."
      : "No probe history available for this service.";
    populateTable(container, service);
  }

  function showLoadError(container) {
    if (!container.isConnected) return;
    const manager = chartManagers[container.id];
    if (manager) manager.hideLoading();
    container.setAttribute("aria-busy", "false");
    const card = container.closest(".healthmon-card");
    card.querySelector("[data-healthmon-chart-error]").classList.remove("d-none");
  }

  function initializeDashboard(root) {
    const region = root.id === LIVE_REGION_ID ? root : root.querySelector("#" + LIVE_REGION_ID);
    if (!region) return;
    const containers = Array.from(region.querySelectorAll("[data-healthmon-heatmap]")).filter(
      function (container) {
        return container.dataset.healthmonEnhanced !== "true";
      },
    );
    if (containers.length === 0) return;
    if (typeof echarts === "undefined" || typeof NocaECharts === "undefined") {
      containers.forEach(showLoadError);
      return;
    }
    containers.forEach(function (container) {
      container.dataset.healthmonEnhanced = "true";
      chartManagers[container.id] = NocaECharts.create(container).showLoading();
    });

    fetch(region.dataset.healthmonUptimeUrl, {
      cache: "no-store",
      headers: { Accept: "application/json" },
    })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (payload) {
        const services = Object.fromEntries(
          payload.services.map(function (service) {
            return [service.key, service];
          }),
        );
        containers.forEach(function (container) {
          if (!container.isConnected) return;
          const service = services[container.dataset.healthmonService];
          if (service) renderChart(container, service);
          else showLoadError(container);
        });
      })
      .catch(function (error) {
        console.error("HealthMonitor: failed to load uptime history.", error);
        containers.forEach(showLoadError);
      });
  }

  function disposeDashboard(root) {
    root.querySelectorAll("[data-healthmon-heatmap]").forEach(function (container) {
      if (!chartManagers[container.id]) return;
      chartManagers[container.id].dispose();
      delete chartManagers[container.id];
    });
  }

  function updateRefreshToggle() {
    const button = document.querySelector("[data-healthmon-refresh-toggle]");
    if (!button) return;
    button.textContent = window.healthmonRefreshPaused
      ? "Resume automatic refresh"
      : "Pause automatic refresh";
    button.setAttribute("aria-pressed", String(window.healthmonRefreshPaused));
  }

  function captureRefreshState() {
    const region = document.getElementById(LIVE_REGION_ID);
    if (!region) return null;
    const expanded = {};
    const details = {};
    region.querySelectorAll("[data-healthmon-service]").forEach(function (card) {
      const key = card.dataset.healthmonService;
      const toggle = card.querySelector(".healthmon-card-toggle");
      expanded[key] = toggle.getAttribute("aria-expanded") === "true";
      details[key] = card.querySelector("[data-healthmon-uptime-details]").open;
    });
    return {
      expanded: expanded,
      details: details,
      focusedId: region.contains(document.activeElement) ? document.activeElement.id : null,
    };
  }

  function restoreRefreshState(state) {
    const region = document.getElementById(LIVE_REGION_ID);
    if (!region || !state) return;
    region.querySelectorAll("[data-healthmon-service]").forEach(function (card) {
      const key = card.dataset.healthmonService;
      if (state.expanded[key] === false) {
        card.querySelector(".healthmon-card-toggle").setAttribute("aria-expanded", "false");
        card.querySelector(".collapse").classList.remove("show");
      }
      card.querySelector("[data-healthmon-uptime-details]").open = state.details[key] === true;
    });
    if (state.focusedId) {
      const focusTarget = document.getElementById(state.focusedId);
      if (focusTarget) focusTarget.focus({ preventScroll: true });
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    let refreshState = null;
    initializeDashboard(document);
    document.body.addEventListener("click", function (event) {
      if (!event.target.closest("[data-healthmon-refresh-toggle]")) return;
      window.healthmonRefreshPaused = !window.healthmonRefreshPaused;
      updateRefreshToggle();
    });
    document.body.addEventListener("htmx:beforeRequest", function (event) {
      const automatic = event.detail.elt.id === LIVE_REGION_ID;
      if (automatic && window.healthmonRefreshPaused) event.preventDefault();
    });
    document.body.addEventListener("htmx:beforeSwap", function (event) {
      if (event.detail.target.id !== LIVE_REGION_ID) return;
      refreshState = captureRefreshState();
      disposeDashboard(event.detail.target);
    });
    document.body.addEventListener("htmx:afterSwap", function (event) {
      if (event.detail.target.id !== LIVE_REGION_ID) return;
      restoreRefreshState(refreshState);
      refreshState = null;
    });
    document.body.addEventListener("htmx:afterSettle", function (event) {
      if (event.detail.target.id !== LIVE_REGION_ID) return;
      const region = document.getElementById(LIVE_REGION_ID);
      if (region) initializeDashboard(region);
    });
    document.body.addEventListener("shown.bs.collapse", function (event) {
      event.target.querySelectorAll("[data-healthmon-heatmap]").forEach(function (container) {
        if (chartManagers[container.id]) chartManagers[container.id].resize();
      });
    });
  });
})();
