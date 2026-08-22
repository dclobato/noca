// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

(function () {
  var hiddenInput = document.getElementById('site_names_input');
  if (!hiddenInput) return;
  var entriesInput = document.getElementById('site_entries_input');
  var removeIconTemplate = document.getElementById('site-remove-icon-template');

  var initialSites = [];
  var initialEntries = [];
  try {
    initialSites = JSON.parse(hiddenInput.value || '[]');
  } catch (_err) {
    initialSites = [];
  }
  try {
    initialEntries = JSON.parse(entriesInput ? entriesInput.value || '[]' : '[]');
  } catch (_err) {
    initialEntries = [];
  }

  var sites = initialEntries.length
    ? initialEntries.map(function (entry) {
        return {
          name: normalizeSiteName(entry.name),
          user_count: Number(entry.user_count) || 0
        };
      })
    : initialSites.map(function (name) {
        return { name: normalizeSiteName(name), user_count: 0 };
      });
  var editable = !!document.getElementById('site-add-btn');

  function normalizeSiteName(name) {
    return String(name || '').trim();
  }

  function siteKey(name) {
    return normalizeSiteName(name).toLocaleLowerCase();
  }

  function syncHiddenInput() {
    hiddenInput.value = JSON.stringify(sites.map(function (site) { return site.name; }));
  }

  function warningEl() {
    return document.getElementById('site-remove-warning');
  }

  function showWarning(msg) {
    var el = warningEl();
    if (!el) return;
    el.textContent = msg;
    el.classList.remove('d-none');
  }

  function clearWarning() {
    var el = warningEl();
    if (!el) return;
    el.textContent = '';
    el.classList.add('d-none');
  }

  function renderSites() {
    var body = document.getElementById('site-table-body');
    if (!body) return;

    body.innerHTML = '';
    sites.forEach(function (site) {
      var row = document.createElement('tr');
      row.className = 'site-row';
      row.innerHTML =
        '<td class="site-name-cell"></td>' +
        '<td class="text-center site-user-count-cell"></td>' +
        '<td class="text-end"></td>';
      row.querySelector('.site-name-cell').textContent = site.name;
      row.querySelector('.site-user-count-cell').textContent = String(site.user_count);
      if (editable) {
        var actionCell = row.querySelector('.text-end');
        var iconHtml = removeIconTemplate ? removeIconTemplate.innerHTML : 'Remove';
        actionCell.innerHTML =
          '<button type="button" ' +
          'class="btn btn-sm btn-outline-danger site-remove-btn">' +
          iconHtml +
          '</button>';
        var removeButton = row.querySelector('.site-remove-btn');
        removeButton.dataset.siteName = site.name;
        removeButton.setAttribute('aria-label', 'Remove site ' + site.name);
      }
      body.appendChild(row);
    });

    syncHiddenInput();
    clearWarning();
  }

  function addSite() {
    var input = document.getElementById('site_add_input');
    if (!input) return;

    var name = normalizeSiteName(input.value);
    if (!name) {
      showWarning('Site name is required.');
      return;
    }

    var key = siteKey(name);
    var exists = sites.some(function (existing) {
      return siteKey(existing.name) === key;
    });
    if (exists) {
      showWarning('A site with that name already exists in this contest.');
      return;
    }

    sites.push({ name: name, user_count: 0 });
    input.value = '';
    renderSites();
  }

  function removeSite(name) {
    if (sites.length <= 1) {
      showWarning('At least one site must remain.');
      return;
    }
    sites = sites.filter(function (existing) {
      return siteKey(existing.name) !== siteKey(name);
    });
    renderSites();
  }

  function resetSites() {
    sites = initialEntries.length
      ? initialEntries.map(function (entry) {
          return {
            name: normalizeSiteName(entry.name),
            user_count: Number(entry.user_count) || 0
          };
        })
      : initialSites.map(function (name) {
          return { name: normalizeSiteName(name), user_count: 0 };
        });
    var input = document.getElementById('site_add_input');
    if (input) input.value = '';
    renderSites();
  }

  document.addEventListener('click', function (event) {
    var addBtn = event.target.closest('#site-add-btn');
    if (addBtn) {
      addSite();
      return;
    }

    var resetBtn = event.target.closest('#site-reset-btn');
    if (resetBtn) {
      resetSites();
      return;
    }

    var removeBtn = event.target.closest('.site-remove-btn');
    if (removeBtn) {
      removeSite(removeBtn.dataset.siteName || '');
    }
  });

  var addInput = document.getElementById('site_add_input');
  if (addInput) {
    addInput.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') {
        event.preventDefault();
        addSite();
      }
    });
  }

  var form = document.getElementById('edit-metadata-form');
  if (form) {
    form.addEventListener('submit', function (event) {
      if (sites.length < 1) {
        event.preventDefault();
        showWarning('At least one site must remain.');
        return;
      }
      syncHiddenInput();
    });
  }

  renderSites();

  if (!form) return;
  var publicationModalElement = document.getElementById('publication-confirm-modal');
  var runningModalElement = document.getElementById('running-contest-confirm-modal');
  var publicationModal = publicationModalElement
    ? new bootstrap.Modal(publicationModalElement)
    : null;
  var runningModal = runningModalElement
    ? new bootstrap.Modal(runningModalElement)
    : null;
  var publicationConfirmed = false;
  var runningChangesConfirmed = false;

  form.addEventListener(
    'invalid',
    function (event) {
      var collapsedSection = event.target.closest('.noca-contest-edit-section');
      if (collapsedSection) collapsedSection.open = true;
    },
    true
  );

  function newlyEnablesPublication() {
    var selected = form.querySelector(
      'input[name="release_problem_set_after_end"]:checked'
    );
    return (
      form.dataset.initialPublication !== 'yes' &&
      selected &&
      selected.value === 'yes'
    );
  }

  form.addEventListener('submit', function (event) {
    if (newlyEnablesPublication() && !publicationConfirmed && publicationModal) {
      event.preventDefault();
      publicationModal.show();
      return;
    }
    if (
      form.dataset.isRunning === 'true' &&
      !runningChangesConfirmed &&
      runningModal
    ) {
      event.preventDefault();
      runningModal.show();
    }
  });

  var confirmPublicationButton = document.getElementById('confirm-publication-btn');
  if (confirmPublicationButton) {
    confirmPublicationButton.addEventListener('click', function () {
      publicationConfirmed = true;
      publicationModal.hide();
      form.requestSubmit();
    });
  }

  var confirmRunningButton = document.getElementById('confirm-running-contest-btn');
  if (confirmRunningButton) {
    confirmRunningButton.addEventListener('click', function () {
      runningChangesConfirmed = true;
      runningModal.hide();
      form.requestSubmit();
    });
  }
})();
