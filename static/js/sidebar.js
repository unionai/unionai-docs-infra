// Center the active page within the sidebar's own scroll container.
//
// The sidebar (`.sidebar`) is re-rendered from the top on every navigation, so
// the active page can sit far below the fold. We expose the centering routine
// on `window` and let the inline script in `sidebar.html` invoke it *before the
// first paint* (see that file), so the sidebar is already scrolled when it first
// appears — no visible snap from the top.
//
// Its height is viewport-driven (`.container` is `height: calc(100dvh - …)` in
// base.css), so the geometry below is stable even before the rest of the page
// has finished laying out.
(function () {
  function centerActiveSidebarItem() {
    const sidebar = document.querySelector('.sidebar');
    if (!sidebar) return;
    const active = sidebar.querySelector('.active');
    if (!active) return;

    // Center the active item, adjusting only the sidebar's own scrollTop so the
    // main page never moves.
    const sidebarRect = sidebar.getBoundingClientRect();
    const activeRect = active.getBoundingClientRect();
    const offset = (activeRect.top - sidebarRect.top)
      - (sidebar.clientHeight / 2)
      + (active.offsetHeight / 2);

    sidebar.scrollTop += offset;
  }

  window.__centerActiveSidebarItem = centerActiveSidebarItem;

  // Web fonts / shoelace components can nudge heights after the first paint.
  // Re-center once everything has loaded — a small correction, not the primary
  // scroll, so it doesn't read as a jump.
  window.addEventListener('load', centerActiveSidebarItem);
})();
