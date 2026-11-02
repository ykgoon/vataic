// Vataic UI enhancements
document.addEventListener('DOMContentLoaded', function() {
  // Auto-refresh status flash
  const flash = document.getElementById('status-flash');
  if (flash) {
    setTimeout(function() { flash.classList.remove('show'); }, 3000);
  }
  // Dashboard row auto-refresh is handled by htmx polling on the tbody
  // (hx-trigger="every 3 seconds"); a JS fetch poller here would double-hit
  // the endpoint and fight htmx's swap handling, so it was removed.
});
