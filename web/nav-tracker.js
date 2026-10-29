// Simple navigation tracking for Seva
// Tracks the last screen before language selection

(function() {
  // Don't track on language page itself
  if (window.location.pathname.includes('select-language')) return;

  try {
    var currentPath = window.location.pathname;
    var currentFile = currentPath.split('/').pop() || 'index.html';

    // Store current page as last screen
    localStorage.setItem('seva.lastScreen', currentFile);

    // Also store full URL for referrer checking
    localStorage.setItem('seva.lastFullUrl', window.location.href);
  } catch(e) {
    // localStorage may be unavailable; ignore
  }
})();