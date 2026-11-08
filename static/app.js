// Ask before submitting any form marked with data-confirm (used for deletes).
// Kept in a file instead of inline handlers so the CSP can forbid inline script.
document.addEventListener("submit", function (event) {
  var message = event.target.getAttribute("data-confirm");
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});
