// Vataic UI enhancements
document.addEventListener('DOMContentLoaded', function() {
  // Auto-refresh status flash
  const flash = document.getElementById('status-flash');
  if (flash) {
    setTimeout(function() { flash.classList.remove('show'); }, 3000);
  }

  // Add htmx polling to job dashboard
  const jobTable = document.querySelector('table.jobs-table');
  if (jobTable) {
    const tbody = jobTable.querySelector('tbody');
    if (tbody) {
      setInterval(function() {
        fetch('/jobs/partial')
          .then(r => r.text())
          .then(html => {
            const doc = new DOMParser().parseFromString(html, 'text/html');
            const rows = doc.querySelectorAll('tr');
            if (rows.length > 0) {
              tbody.innerHTML = '';
              rows.forEach(row => tbody.appendChild(row.cloneNode(true)));
            }
          })
          .catch(function() {});
      }, 5000);
    }
  }
});