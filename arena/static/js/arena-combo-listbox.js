//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Shared controller for the Arena `.arena-combo` pattern: an input that renders
// its own listbox of server-supplied options. It owns the ARIA state, keyboard
// navigation, live-region announcements, debounce, request cancellation, and
// option rendering; callers supply only what differs (how to search, how an
// option looks, and what selecting one does).
//
// Two behaviors here are deliberate, and each exists because the obvious
// implementation is wrong:
//
//   1. Closing cancels. A queued debounce or an in-flight request outlives the
//      close that a blur or Escape performs, and its response would reopen the
//      listbox after focus left or after the user dismissed it. `close()`
//      therefore clears the timer, aborts the request, and drops the token every
//      pending response is checked against, so a late response renders nothing.
//   2. The listbox is not a native `<datalist>`. A datalist re-filters the given
//      options by substring against the typed text, which silently discarded
//      server matches that ranked on stemmed, out-of-order terms. The caller's
//      search result is displayed exactly as returned.
//
// Exported as UMD: `window.ArenaComboListbox` / `module.exports`.
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.ArenaComboListbox = api;
  }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  /**
   * Wire one input to its listbox.
   *
   * Required deps: { input, listbox, search(query, signal) -> Promise<items> }.
   * Optional deps: { doc, statusEl, minLength, debounceMs, optionIdPrefix,
   *                  renderOption(item) -> Node|string, onSelect(item),
   *                  emptyMessage() -> string, loadingMessage, errorMessage,
   *                  announceCount(count) -> string, shouldIgnoreInput() -> bool,
   *                  isSearchable(query) -> bool, tooShortMessage(query) -> string|null,
   *                  outsideClickRoot, closeOnBlur }
   */
  function createComboListbox(deps) {
    var input = deps.input;
    var listbox = deps.listbox;
    var statusEl = deps.statusEl || null;
    var doc = deps.doc || (typeof document !== "undefined" ? document : null);
    var minLength = typeof deps.minLength === "number" ? deps.minLength : 1;
    var debounceMs = typeof deps.debounceMs === "number" ? deps.debounceMs : 250;
    var optionIdPrefix = deps.optionIdPrefix || "combo-option";
    var loadingMessage = deps.loadingMessage || "Loading…";
    var errorMessage = deps.errorMessage || "Could not load results. Try again.";

    var timer = null;
    var controller = null;
    var items = [];
    var options = [];
    var activeIndex = -1;

    function announce(message) {
      if (!statusEl) return;
      // Re-announce an identical message by clearing first; a live region that
      // is assigned the same text says nothing.
      statusEl.textContent = "";
      setTimeout(function () {
        statusEl.textContent = message;
      }, 0);
    }

    function cancelPending() {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      if (controller) {
        controller.abort();
        controller = null;
      }
    }

    function close() {
      // Cancelling here is what keeps a late response from reopening a listbox
      // the user has already dismissed or navigated away from.
      cancelPending();
      listbox.classList.remove("open");
      listbox.replaceChildren();
      items = [];
      options = [];
      activeIndex = -1;
      input.setAttribute("aria-expanded", "false");
      input.setAttribute("aria-activedescendant", "");
    }

    function isOpen() {
      return listbox.classList.contains("open");
    }

    function setActive(index) {
      if (!options.length) return;
      activeIndex = Math.max(0, Math.min(index, options.length - 1));
      options.forEach(function (option, optionIndex) {
        var active = optionIndex === activeIndex;
        option.classList.toggle("is-active", active);
        option.setAttribute("aria-selected", active ? "true" : "false");
      });
      var activeOption = options[activeIndex];
      input.setAttribute("aria-activedescendant", activeOption.id);
      if (typeof activeOption.scrollIntoView === "function") {
        activeOption.scrollIntoView({ block: "nearest" });
      }
    }

    function openWith(children) {
      listbox.replaceChildren();
      children.forEach(function (child) {
        listbox.appendChild(child);
      });
      listbox.classList.add("open");
      activeIndex = -1;
      input.setAttribute("aria-expanded", "true");
      input.setAttribute("aria-activedescendant", "");
    }

    function showMessage(message) {
      var node = doc.createElement("div");
      node.className = "arena-combo-dropdown-empty";
      node.setAttribute("role", "option");
      node.setAttribute("aria-disabled", "true");
      node.textContent = message;
      items = [];
      options = [];
      openWith([node]);
      // The loading state is announced like any other: the live region is
      // polite, so a burst of keystrokes coalesces rather than interrupting.
      announce(message);
    }

    function select(index) {
      var item = items[index];
      if (item === undefined) return;
      if (deps.onSelect) deps.onSelect(item);
    }

    function render(results) {
      items = results;
      options = [];

      if (!results.length) {
        showMessage(deps.emptyMessage ? deps.emptyMessage() : "No matches found.");
        return;
      }

      results.forEach(function (item, index) {
        var option = doc.createElement("button");
        option.type = "button";
        option.className = "arena-combo-dropdown-item";
        option.id = optionIdPrefix + "-" + index;
        option.tabIndex = -1;
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", "false");

        var content = deps.renderOption ? deps.renderOption(item) : String(item);
        if (typeof content === "string") {
          // textContent, never innerHTML: option text is server data.
          option.textContent = content;
        } else {
          option.appendChild(content);
        }

        // Keep focus in the input so the blur handler does not close the
        // listbox before the click lands on the option.
        option.addEventListener("pointerdown", function (event) {
          if (event && typeof event.preventDefault === "function") event.preventDefault();
        });
        option.addEventListener("click", function () {
          select(index);
        });

        options.push(option);
      });

      openWith(options);
      announce(
        deps.announceCount ? deps.announceCount(results.length) : results.length + " result" + (results.length === 1 ? "" : "s") + " available."
      );
    }

    function load(query) {
      controller = new AbortController();
      var requestController = controller;
      showMessage(loadingMessage);

      return Promise.resolve()
        .then(function () {
          return deps.search(query, requestController.signal);
        })
        .then(function (results) {
          // A superseded or cancelled request must not paint: `controller` is
          // nulled by close() and replaced by the next keystroke.
          if (requestController !== controller) return;
          controller = null;
          render(Array.isArray(results) ? results : []);
        })
        .catch(function (error) {
          if (error && error.name === "AbortError") return;
          if (requestController !== controller) return;
          controller = null;
          showMessage(errorMessage);
        });
    }

    function isSearchable(query) {
      return deps.isSearchable ? deps.isSearchable(query) : query.length >= minLength;
    }

    function handleInput() {
      // A caller that writes the input's value programmatically (a selection)
      // suppresses the search that its synthetic `input` event would restart.
      if (deps.shouldIgnoreInput && deps.shouldIgnoreInput()) return;
      cancelPending();
      var query = input.value.trim();
      if (!isSearchable(query)) {
        // Say why nothing is being searched rather than showing an empty
        // result the user would read as "there is nothing to find".
        var hint = deps.tooShortMessage ? deps.tooShortMessage(query) : null;
        if (hint) {
          showMessage(hint);
        } else {
          close();
        }
        return;
      }
      timer = setTimeout(function () {
        timer = null;
        void load(query);
      }, debounceMs);
    }

    function handleKeydown(event) {
      if (event.key === "Escape") {
        if (!isOpen()) return;
        event.preventDefault();
        close();
        return;
      }
      if ((event.key === "ArrowDown" || event.key === "ArrowUp") && options.length) {
        event.preventDefault();
        var step = event.key === "ArrowDown" ? 1 : -1;
        var start = activeIndex < 0 ? (step > 0 ? 0 : options.length - 1) : activeIndex + step;
        setActive(start);
      } else if (event.key === "Enter" && activeIndex >= 0 && options[activeIndex]) {
        // Enter is swallowed only while an option is highlighted, so the key
        // still submits the surrounding form otherwise.
        event.preventDefault();
        select(activeIndex);
      }
    }

    input.addEventListener("input", handleInput);
    input.addEventListener("keydown", handleKeydown);
    if (deps.closeOnBlur) {
      // Option pointerdown is prevented, so a blur here means focus truly left.
      input.addEventListener("blur", close);
    }
    if (deps.outsideClickRoot && doc && typeof doc.addEventListener === "function") {
      doc.addEventListener("click", function (event) {
        if (!deps.outsideClickRoot.contains(event.target)) close();
      });
    }

    return {
      close: close,
      isOpen: isOpen,
      handleInput: handleInput,
      handleKeydown: handleKeydown,
      announce: announce,
      activeIndex: function () {
        return activeIndex;
      },
      optionCount: function () {
        return options.length;
      },
    };
  }

  return { createComboListbox: createComboListbox };
});
