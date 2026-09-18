// Keep the sidebar visually still across navigations.
//
// Every click is a full page load, so `.sidebar` is rebuilt from the top and
// would otherwise appear at scrollTop 0. We used to re-center the active item
// on every load, which put the panel at a *different* offset on every
// navigation: the link you just clicked visibly slid across the panel, and the
// deeper into the tree you were, the bigger the lurch (DOC-1352).
//
// Instead we pin the item that was clicked. On a sidebar click we record where
// that item sat inside the panel; on the next page we restore exactly that
// position, so the link stays put under the cursor and the tree around it does
// not move. Centering survives only as the fallback for arrivals with no
// recorded position — a fresh tab, a search result, an in-content link.
//
// The routine is exposed on `window` and invoked by the inline script in
// `sidebar.html` *before the first paint* (see that file), so the sidebar is
// already positioned when it first appears. Its height is viewport-driven
// (`.container` is `height: calc(100dvh - …)` in base.css), so the geometry
// below is stable even before the rest of the page has finished laying out.
(function () {
  const STORAGE_KEY = 'sidebarScrollPosition';

  // How far inside the panel edges the active page must sit before we accept a
  // restored position; closer than this and we center it instead.
  const VISIBLE_MARGIN = 24;

  // Sub-pixel differences aren't worth a correction (and a correction that
  // small would only read as jitter).
  const CORRECTION_THRESHOLD = 2;

  // The scrollTop we last set ourselves. Anything else means the reader has
  // scrolled the panel, and we leave their position alone.
  let appliedTop = null;

  // Whether we ever positioned the panel on this page load. Distinct from
  // `appliedTop`, which a manual scroll clears.
  let applied = false;

  function panel() {
    return document.querySelector('.sidebar');
  }

  // The desktop tree only. `.sidebar` also contains the mobile drawer's copy of
  // the whole tree (`sl-drawer.drawer-sidebar`, emitted first and `hidden`
  // until the custom element upgrades and moves it to <body>). Querying the
  // panel directly matches that hidden copy first, and a hidden element's rects
  // are all zero — which is why the pre-paint pass used to compute a negative
  // offset, clamp to 0, and leave every bit of the positioning to a post-paint
  // pass. Scope every lookup to the desktop wrapper instead.
  function tree(el) {
    return el.querySelector('.sidebar-items') || el;
  }

  function readState() {
    try { return JSON.parse(sessionStorage.getItem(STORAGE_KEY)) || null; }
    catch (e) { return null; }
  }

  function writeState(state) {
    try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state)); }
    catch (e) { /* private mode / quota — ignore */ }
  }

  // The current page's own link. `.active` alone is ambiguous: the ancestor
  // section title carries it too and comes first in DOM order, so a bare
  // `querySelector('.active')` centers the section header instead of the page
  // you are on.
  function activeItem(el) {
    const scope = tree(el);
    const links = scope.querySelectorAll('a');
    for (let i = 0; i < links.length; i++) {
      if (links[i].pathname === location.pathname) return links[i];
    }
    return scope.querySelector('.active.page-link') || scope.querySelector('.active');
  }

  function linkTo(el, href) {
    const links = tree(el).querySelectorAll('a');
    for (let i = 0; i < links.length; i++) {
      if (links[i].getAttribute('href') === href) return links[i];
    }
    return null;
  }

  // Distance from the top of the scrollable content to `item`.
  function offsetOf(el, item) {
    return el.scrollTop + (item.getBoundingClientRect().top - el.getBoundingClientRect().top);
  }

  function clamp(el, top) {
    return Math.max(0, Math.min(top, el.scrollHeight - el.clientHeight));
  }

  function centerOn(el, item) {
    return offsetOf(el, item) - (el.clientHeight / 2) + (item.offsetHeight / 2);
  }

  // Would `item` be comfortably inside the panel if we scrolled to `top`?
  function visibleAt(el, item, top) {
    const start = offsetOf(el, item);
    return start >= top + VISIBLE_MARGIN
      && (start + item.offsetHeight) <= (top + el.clientHeight - VISIBLE_MARGIN);
  }

  function desiredTop(el) {
    const state = readState();
    const active = activeItem(el);

    // 1. Pin the clicked item where it was. This is the common case: clicking
    //    through the tree should not move the tree.
    if (state && state.href) {
      const clicked = linkTo(el, state.href);
      if (clicked) {
        const top = clamp(el, offsetOf(el, clicked) - state.y);
        if (!active || visibleAt(el, active, top)) return top;
      }
    }

    // 2. No usable anchor, but we know where the panel was left (a reload, or
    //    back/forward). Keep that, as long as the current page is in view.
    if (state && typeof state.top === 'number') {
      const top = clamp(el, state.top);
      if (!active || visibleAt(el, active, top)) return top;
    }

    // 3. Cold arrival, or the current page would be off-screen: center it.
    if (active) return clamp(el, centerOn(el, active));

    return el.scrollTop;
  }

  function restoreSidebarScroll() {
    const el = panel();
    if (!el) return;
    const want = desiredTop(el);
    el.scrollTop = want;
    appliedTop = el.scrollTop;
    applied = true;
  }

  window.__restoreSidebarScroll = restoreSidebarScroll;

  // Bring the current page into view, but only if it is not already there: the
  // breadcrumb's "where am I?" link should not shove a panel the reader is
  // happy with. Centring is the same fallback a cold arrival uses.
  window.__revealActiveSidebarItem = function () {
    const el = panel();
    if (!el) return;
    const active = activeItem(el);
    if (!active || visibleAt(el, active, el.scrollTop)) return;
    el.scrollTop = clamp(el, centerOn(el, active));
    appliedTop = el.scrollTop;
    applied = true;
  };

  // Forget the remembered scroll position. Part of the logo's "start over"
  // reset, so the next page is positioned from scratch rather than from an
  // anchor recorded before the reset.
  window.__forgetSidebarScroll = function () {
    try { sessionStorage.removeItem(STORAGE_KEY); } catch (e) { /* ignore */ }
    appliedTop = null;
  };

  // Record where a clicked link sits inside the panel, so the next page can put
  // it back in the same place. Capture phase: the collapse handler in
  // `sidebar.html` may expand a section on the way through, and this must be
  // recorded against the geometry the reader actually saw.
  document.addEventListener('click', function (e) {
    const link = e.target.closest && e.target.closest('a');
    if (!link) return;
    const el = panel();
    if (!el || !tree(el).contains(link)) return;
    const href = link.getAttribute('href');
    if (!href || href.charAt(0) === '#') return;
    writeState({
      href: href,
      y: link.getBoundingClientRect().top - el.getBoundingClientRect().top,
      top: el.scrollTop
    });
  }, true);

  // A manual scroll invalidates the click anchor: the reader has told us where
  // they want the panel. Programmatic scrolls fire this too, so ignore the ones
  // we caused.
  document.addEventListener('scroll', function (e) {
    const el = panel();
    if (!el || e.target !== el) return;
    if (appliedTop !== null && Math.abs(el.scrollTop - appliedTop) <= CORRECTION_THRESHOLD) return;
    appliedTop = null;
    writeState({ top: el.scrollTop });
  }, true);

  // Safety net. If the inline call never landed (the script failed to load, or
  // the sidebar was not in the DOM yet), position the panel the first time the
  // page is actually looked at rather than leaving it stuck at the top.
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible' && !applied) restoreSidebarScroll();
  });

  // Web fonts can nudge item heights after the first paint. Re-apply the same
  // position once they have settled — but only if the reader has not scrolled in
  // the meantime, and only when it actually moves something, so this can never
  // become a second visible jump.
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(function () {
      const el = panel();
      if (!el || appliedTop === null) return;
      if (Math.abs(el.scrollTop - appliedTop) > CORRECTION_THRESHOLD) return;
      const top = desiredTop(el);
      if (Math.abs(top - el.scrollTop) < CORRECTION_THRESHOLD) return;
      el.scrollTop = top;
      appliedTop = el.scrollTop;
    });
  }
})();
