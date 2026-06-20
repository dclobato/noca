/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/**
 * Personal & Security tab — manages pending state for all personal data fields
 * (name, date of birth, location, language, affiliation), inline subdivision loading, browser
 * auto-detect, affiliation modal intercept, and unsaved-changes guards.
 *
 * Save is deferred: all changes are held in form controls and a pendingAffiliation
 * state object, then committed atomically via POST /user/profile/personal-data.
 */
(() => {
  "use strict";

  // ── DOM references ──────────────────────────────────────────────────────────

  const tabPane = document.getElementById("personal-security-tab-pane");
  if (!tabPane) {
    return;
  }

  const personalDataUrl = tabPane.dataset.personalDataUrl;
  const subdivisionsUrl = tabPane.dataset.subdivisionsUrl;
  const detectUrl = tabPane.dataset.detectUrl;

  const nameInput = document.getElementById("profile-name-input");
  const nameError = document.getElementById("profile-name-error");
  const dateOfBirthInput = document.getElementById("profile-date-of-birth-input");
  const rankingVisibleInput = document.getElementById("profile-ranking-visible-input");
  const dateOfBirthError = document.getElementById("profile-date-of-birth-error");
  const languageSelect = document.getElementById("profile-language-select");
  const preferedLanguageSelect = document.getElementById("profile-prefered-language-select");
  const countrySelect = document.getElementById("profile-country-select");
  const subdivisionSelect = document.getElementById("profile-subdivision-select");
  const detectButton = document.getElementById("profile-location-detect");
  const locationDetectedAlert = document.getElementById("profile-location-detected");
  const locationErrorAlert = document.getElementById("profile-location-error");
  const affiliationDisplay = document.getElementById("profile-affiliation-display");
  const affiliationModal = document.getElementById("profileAffiliationModal");
  const affiliationSearch = document.getElementById("profile-affiliation-search");
  const affiliationResults = document.getElementById("profile-affiliation-results");
  const affiliationClear = document.getElementById("profile-affiliation-clear");
  const affiliationError = document.getElementById("profile-affiliation-error");
  const saveButton = document.getElementById("profile-personal-save");
  const errorAlert = document.getElementById("profile-personal-error");
  const successAlert = document.getElementById("profile-personal-success");
  const headerName = document.getElementById("profile-header-name");
  const dirtyDot = document.getElementById("personal-security-dirty-dot");

  // ── Pending state ────────────────────────────────────────────────────────────

  let isDirty = false;
  let pendingAffiliationId = affiliationModal ? (affiliationModal.dataset.affiliationId || null) : null;
  let pendingAffiliationName = affiliationModal ? (affiliationModal.dataset.affiliationName || null) : null;

  const markDirty = () => {
    isDirty = true;
    if (successAlert) {
      successAlert.classList.add("d-none");
    }
    if (dirtyDot) {
      dirtyDot.classList.remove("d-none");
    }
  };

  const markClean = () => {
    isDirty = false;
    if (dirtyDot) {
      dirtyDot.classList.add("d-none");
    }
  };

  // ── Helpers ──────────────────────────────────────────────────────────────────

  const jsonHeaders = { "Content-Type": "application/json", Accept: "application/json" };

  const setAlert = (element, message) => {
    if (!element) {
      return;
    }
    element.textContent = message || "";
    element.classList.toggle("d-none", !message);
  };

  const postJson = async (url, payload) => {
    const response = await fetch(url, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      throw Object.assign(new Error(data.message || data.error || "Request failed."), {
        field: data.error,
      });
    }
    return data;
  };

  const closeModal = (element) => {
    const modal = window.bootstrap?.Modal?.getInstance(element);
    if (modal) {
      modal.hide();
    }
  };

  // ── Subdivision loader ───────────────────────────────────────────────────────

  const renderSubdivisions = (items, selectedCode) => {
    subdivisionSelect.replaceChildren(new Option("Subdivision (not set)", ""));
    items.forEach((item) => {
      const option = new Option(item.name, item.code);
      option.selected = item.code === selectedCode;
      subdivisionSelect.add(option);
    });
  };

  const loadSubdivisions = async (selectedCode = "") => {
    if (!countrySelect.value) {
      renderSubdivisions([], "");
      return;
    }
    const url = `${subdivisionsUrl}?country_code=${encodeURIComponent(countrySelect.value)}`;
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Could not load subdivisions.");
    }
    renderSubdivisions(data.subdivisions || [], selectedCode);
  };

  // Load subdivisions on page load using the server-rendered current value
  loadSubdivisions(subdivisionSelect.dataset.currentSubdivision || "").catch((err) =>
    setAlert(locationErrorAlert, err.message),
  );

  // ── Dirty tracking ───────────────────────────────────────────────────────────

  nameInput.addEventListener("input", markDirty);
  dateOfBirthInput.addEventListener("change", markDirty);
  rankingVisibleInput?.addEventListener("change", markDirty);
  languageSelect.addEventListener("change", markDirty);
  preferedLanguageSelect.addEventListener("change", markDirty);
  subdivisionSelect.addEventListener("change", markDirty);

  countrySelect.addEventListener("change", () => {
    markDirty();
    setAlert(locationErrorAlert, "");
    loadSubdivisions().catch((err) => setAlert(locationErrorAlert, err.message));
  });

  // ── Auto-detect location ─────────────────────────────────────────────────────

  if (detectButton) {
    detectButton.addEventListener("click", () => {
      setAlert(locationErrorAlert, "");
      setAlert(locationDetectedAlert, "");
      if (!navigator.geolocation) {
        setAlert(locationErrorAlert, "Browser geolocation is not available.");
        return;
      }
      detectButton.disabled = true;
      navigator.geolocation.getCurrentPosition(
        async (position) => {
          try {
            const data = await postJson(detectUrl, {
              latitude: position.coords.latitude,
              longitude: position.coords.longitude,
            });
            if (!data.country_code) {
              setAlert(locationErrorAlert, "Location could not be detected.");
              return;
            }
            countrySelect.value = data.country_code;
            await loadSubdivisions(data.subdivision_code || "");
            const label = data.subdivision_name
              ? `${data.country_name}, ${data.subdivision_name}`
              : data.country_name;
            setAlert(locationDetectedAlert, `Detected ${label}. Save changes to confirm.`);
            markDirty();
          } catch (err) {
            setAlert(locationErrorAlert, err.message);
          } finally {
            detectButton.disabled = false;
          }
        },
        () => {
          detectButton.disabled = false;
          setAlert(locationErrorAlert, "Browser location permission was denied or unavailable.");
        },
        { enableHighAccuracy: false, maximumAge: 600000, timeout: 10000 },
      );
    });
  }

  // ── Affiliation pending indicator ────────────────────────────────────────────

  const updateAffiliationDisplay = (name) => {
    if (!affiliationDisplay) {
      return;
    }
    if (!name) {
      affiliationDisplay.innerHTML = '<span class="text-arena-muted">Not set</span>';
      return;
    }
    affiliationDisplay.textContent = name;
  };

  const setPendingAffiliation = (id, name) => {
    pendingAffiliationId = id || null;
    pendingAffiliationName = name || null;
    updateAffiliationDisplay(pendingAffiliationName);
    // Show a pending indicator (*) next to the affiliation name so the user
    // knows this change has not been saved yet.
    if (affiliationDisplay) {
      let indicator = affiliationDisplay.parentElement?.querySelector(
        ".arena-pending-indicator",
      );
      if (pendingAffiliationId !== null || pendingAffiliationName !== null) {
        if (!indicator) {
          indicator = document.createElement("span");
          indicator.className = "arena-pending-indicator";
          indicator.textContent = "*";
          indicator.title = "Unsaved change";
          affiliationDisplay.after(indicator);
        }
      } else if (indicator) {
        indicator.remove();
      }
    }
    markDirty();
  };

  // ── Affiliation modal intercept ──────────────────────────────────────────────

  if (affiliationModal) {
    const searchUrl = affiliationModal.dataset.searchUrl;
    const semAfiliacaoId = affiliationModal.dataset.semAfiliacaoId || null;
    const noAffiliationCheckbox = document.getElementById("profile-affiliation-no-affiliation");
    let searchTimer = null;

    const renderResults = (items) => {
      affiliationResults.replaceChildren();
      if (items.length === 0) {
        const empty = document.createElement("div");
        empty.className = "list-group-item text-arena-muted";
        empty.textContent = "No affiliations found.";
        affiliationResults.appendChild(empty);
        return;
      }
      items.forEach((item) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "list-group-item list-group-item-action";
        const meta = [item.subdivision_name, item.country_name].filter(Boolean).join(", ");
        button.textContent = meta ? `${item.name} (${meta})` : item.name;
        button.addEventListener("click", () => {
          const action = `Use "${item.name}" as your affiliation?`;
          if (!window.confirm(action)) {
            return;
          }
          setPendingAffiliation(item.id, item.name);
          closeModal(affiliationModal);
        });
        affiliationResults.appendChild(button);
      });
    };

    const searchAffiliations = async () => {
      setAlert(affiliationError, "");
      const query = affiliationSearch.value.trim();
      affiliationResults.replaceChildren();
      if (query.length < 2) {
        return;
      }
      const response = await fetch(`${searchUrl}?q=${encodeURIComponent(query)}`, {
        headers: { Accept: "application/json" },
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || "Could not search affiliations.");
      }
      renderResults(data.affiliations || []);
    };

    affiliationSearch.addEventListener("input", () => {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(() => {
        searchAffiliations().catch((err) => setAlert(affiliationError, err.message));
      }, 250);
    });

    affiliationClear.addEventListener("click", () => {
      if (!window.confirm("Clear your affiliation?")) {
        return;
      }
      setPendingAffiliation(null, null);
      closeModal(affiliationModal);
    });

    if (noAffiliationCheckbox) {
      noAffiliationCheckbox.addEventListener("change", () => {
        if (noAffiliationCheckbox.checked) {
          setPendingAffiliation(semAfiliacaoId, "Sem afiliação");
          closeModal(affiliationModal);
        } else {
          affiliationSearch.value = "";
          affiliationResults.replaceChildren();
        }
      });
    }

    affiliationModal.addEventListener("show.bs.modal", () => {
      setAlert(affiliationError, "");
      affiliationSearch.value = "";
      affiliationResults.replaceChildren();
      if (noAffiliationCheckbox) {
        noAffiliationCheckbox.checked =
          semAfiliacaoId !== null && pendingAffiliationId === semAfiliacaoId;
      }
    });
  }

  // ── Form submit ──────────────────────────────────────────────────────────────

  const form = document.getElementById("personal-data-form");
  if (form) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      setAlert(errorAlert, "");
      setAlert(successAlert, "");

      // Client-side name validation
      if (nameInput) {
        nameInput.classList.remove("is-invalid");
        if (nameError) {
          nameError.textContent = "";
        }
        const name = nameInput.value.trim();
        if (!name || name.length > 120) {
          nameInput.classList.add("is-invalid");
          if (nameError) {
            nameError.textContent = "Name is required (max 120 characters).";
          }
          nameInput.focus();
          return;
        }
      }
      if (dateOfBirthInput) {
        dateOfBirthInput.classList.remove("is-invalid");
        if (dateOfBirthError) {
          dateOfBirthError.textContent = "";
        }
        if (!dateOfBirthInput.value) {
          dateOfBirthInput.classList.add("is-invalid");
          if (dateOfBirthError) {
            dateOfBirthError.textContent = "Date of birth is required.";
          }
          dateOfBirthInput.focus();
          return;
        }
      }

      if (saveButton) {
        saveButton.disabled = true;
      }

      try {
        const data = await postJson(personalDataUrl, {
          name: nameInput ? nameInput.value.trim() : "",
          date_of_birth: dateOfBirthInput ? dateOfBirthInput.value : "",
          country_code: countrySelect ? (countrySelect.value || null) : null,
          subdivision_code: subdivisionSelect ? (subdivisionSelect.value || null) : null,
          affiliation_id: pendingAffiliationId,
          language_id: languageSelect ? (languageSelect.value || null) : null,
          prefered_language: preferedLanguageSelect ? preferedLanguageSelect.value : "en-US",
          ranking_visible: rankingVisibleInput ? rankingVisibleInput.checked : true,
        });

        markClean();

        if (data.redirect_url) {
          window.location.assign(data.redirect_url);
          return;
        }

        // Remove pending affiliation indicator after successful save
        const indicator = affiliationDisplay?.parentElement?.querySelector(
          ".arena-pending-indicator",
        );
        if (indicator) {
          indicator.remove();
        }

        // Update the profile header name
        if (headerName && data.name) {
          headerName.textContent = data.name;
        }

        // Sync ranking visibility checkbox from persisted server value
        if (rankingVisibleInput && data.ranking_visible !== undefined) {
          rankingVisibleInput.checked = !!data.ranking_visible;
        }

        // Update affiliation modal data attributes to reflect saved state
        if (affiliationModal) {
          affiliationModal.dataset.affiliationId = data.affiliation_id || "";
          affiliationModal.dataset.affiliationName = data.affiliation_name || "";
        }

        setAlert(successAlert, "Changes saved successfully.");
      } catch (err) {
        if (err.field === "name" && nameInput) {
          nameInput.classList.add("is-invalid");
          if (nameError) {
            nameError.textContent = err.message;
          }
        } else if (err.field === "date_of_birth" && dateOfBirthInput) {
          dateOfBirthInput.classList.add("is-invalid");
          if (dateOfBirthError) {
            dateOfBirthError.textContent = err.message;
          }
        } else {
          setAlert(errorAlert, err.message);
        }
      } finally {
        if (saveButton) {
          saveButton.disabled = false;
        }
      }
    });
  }

  // ── Unsaved-changes guard ────────────────────────────────────────────────────

  // Bootstrap tabs do not destroy the DOM when switching — form values are
  // preserved in the hidden pane. No warning is needed for tab switches.
  // Only warn when navigating away from the page entirely.
  window.addEventListener("beforeunload", (event) => {
    if (isDirty) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
})();
