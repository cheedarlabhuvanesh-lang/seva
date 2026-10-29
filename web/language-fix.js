// Language navigation and interface system for Seva
(function() {
  // Get return destination from URL or localStorage
  function getReturnDestination() {
    var params = new URLSearchParams(window.location.search);
    var returnTo = params.get('return');

    if (!returnTo) {
      try {
        // Try to reconstruct from history
        var lastScreen = localStorage.getItem('seva.lastScreen');
        if (lastScreen && lastScreen !== 'select-language.html') {
          returnTo = lastScreen;
        }
      } catch(e) {}
    }

    // Validate and normalize
    if (!returnTo || returnTo === 'select-language.html' || returnTo === 'index.html') {
      returnTo = 'home.html';
    }

    // Clean up path
    if (returnTo.startsWith('/')) returnTo = returnTo.substring(1);
    if (returnTo.startsWith('app/')) returnTo = returnTo.substring(4);

    return returnTo;
  }

  // Update language links to preserve return destination
  function updateLanguageLinks() {
    var returnTo = getReturnDestination();
    var currentUrl = new URL(window.location.href);

    // Update language links on page
    document.querySelectorAll('a[href*="select-language.html"]').forEach(function(link) {
      var url = new URL(link.href);
      url.searchParams.set('return', returnTo);
      link.href = url.toString();
    });
  }

  // Initialize on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', updateLanguageLinks);
  } else {
    updateLanguageLinks();
  }

  // Export function for select-language.html
  window.sevaLanguage = {
    getReturnDestination: getReturnDestination
  };
})();