document.addEventListener("submit", function (event) {
  var form = event.target;
  if (!(form instanceof HTMLFormElement)) return;

  var message = form.dataset.confirm;
  if (!message) return;

  if (!window.confirm(message)) {
    event.preventDefault();
  }
});
