// Display a banner when the user lands on a page after a 404 walk-up.
//
// The walk-up handler in four-o-four.html sets these query params on the
// landed-on page:
//   ?404=<original URL>            — always set (when the walk-up runs)
//   ?source=<original page title>  — only on cross-variant switches
//   ?variant=<from variant>        — only on cross-variant switches
//   ?targetVariant=<to variant>    — only on cross-variant switches
//
// For cross-variant switches we also stash the same info in sessionStorage
// (set by handleVariantChange in header.html). This survives client-side
// redirects (e.g. the home-page redirect shortcode) that may drop the query
// string. The sessionStorage entry is cleared after we display the banner.
//
// If targetVariant is set (via either source), show the cross-variant-switch
// banner. Otherwise show the default "we brought you to the closest page"
// banner.

const VARIANT_SWITCH_TTL_MS = 60 * 1000;

function variantDisplayName(slug) {
    if (!slug) return '';
    // Handle special cases that aren't simple capitalizations.
    const specialCases = { byoc: 'BYOC', oss: 'OSS' };
    if (specialCases[slug.toLowerCase()]) return specialCases[slug.toLowerCase()];
    return slug.charAt(0).toUpperCase() + slug.slice(1);
}

function isSafeVariant(v) {
    return typeof v === 'string' && /^[a-zA-Z0-9-]{1,32}$/.test(v);
}

function readVariantSwitchFromStorage() {
    try {
        const raw = sessionStorage.getItem('variantSwitch');
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object') return null;
        if (parsed.ts && Date.now() - parsed.ts > VARIANT_SWITCH_TTL_MS) {
            sessionStorage.removeItem('variantSwitch');
            return null;
        }
        return parsed;
    } catch (e) {
        return null;
    }
}

function clearVariantSwitchFromStorage() {
    try { sessionStorage.removeItem('variantSwitch'); } catch (e) { /* noop */ }
}

const AUTO_DISMISS_MS = 12000;

// Set the path element's text, abbreviating it with ellipses if it would
// overflow on a single line. Tries progressively more aggressive truncations
// and keeps the longest that fits:
//
//   Phase 1 — drop middle path segments (slash-delimited), always preserving
//             the last segment.    e.g. /docs/v2/…/some-page-name
//   Phase 2 — shrink the last segment by hyphen-delimited chunks, always
//             preserving the last chunk.   e.g. /…/some-very-…-name
//   Phase 3 — character-level middle truncation as a final fallback (covers
//             single-segment paths and pathological no-separator cases).
//
// Requires the element to be laid out (not [hidden]) and to have a definite
// width so clientWidth is stable across measurements. SLACK_PX guards against
// sub-pixel rounding that would otherwise let `overflow: hidden` silently clip
// the trailing glyph.
function setPathWithSmartEllipsis(el, fullText) {
    const SLACK_PX = 4;
    const MIN_USABLE_WIDTH = 32; // px
    const E = '…';
    // If clientWidth is implausibly small (e.g. layout hasn't settled, the
    // close-button custom element hasn't rendered yet), bail out and leave the
    // full text — the parent's `overflow: hidden` will clip the tail rather
    // than letting the algorithm collapse to just "…".
    const fits = () => el.clientWidth >= MIN_USABLE_WIDTH
        && el.scrollWidth <= el.clientWidth - SLACK_PX;

    el.textContent = fullText;
    if (el.clientWidth < MIN_USABLE_WIDTH) return;
    if (fits()) return;

    const segments = fullText.split('/').filter(Boolean);
    const last = segments[segments.length - 1] || '';

    // Phase 1: drop middle path segments.
    if (segments.length >= 3) {
        for (let keepLeading = segments.length - 2; keepLeading >= 0; keepLeading--) {
            const prefix = keepLeading === 0
                ? `/${E}/`
                : `/${segments.slice(0, keepLeading).join('/')}/${E}/`;
            el.textContent = prefix + last;
            if (fits()) return;
        }
    }

    // Phase 2: shrink the last segment by hyphen chunks. Use `/…/` as the
    // leading prefix when phase 1 had segments to drop, otherwise just `/`.
    const tailPrefix = segments.length >= 3 ? `/${E}/` : '/';
    const chunks = last.split('-');
    if (chunks.length >= 3) {
        const lastChunk = chunks[chunks.length - 1];
        for (let keepLeading = chunks.length - 2; keepLeading >= 0; keepLeading--) {
            const truncatedLast = keepLeading === 0
                ? `${E}-${lastChunk}`
                : `${chunks.slice(0, keepLeading).join('-')}-${E}-${lastChunk}`;
            el.textContent = tailPrefix + truncatedLast;
            if (fits()) return;
        }
    }

    // Phase 3: character-level binary search on the full path.
    let lo = 1, hi = fullText.length;
    let best = E;
    while (lo <= hi) {
        const total = (lo + hi) >> 1;
        const head = total >> 1;
        const tail = total - head;
        el.textContent = fullText.slice(0, head) + E + fullText.slice(fullText.length - tail);
        if (fits()) {
            best = el.textContent;
            lo = total + 1;
        } else {
            hi = total - 1;
        }
    }
    el.textContent = best;
}

function showNotice(notice) {
    // Caller is responsible for unhiding (notice.hidden = false) before this
    // so any measurements (e.g. middle-truncating the path) can happen first.
    // Force a reflow so the transition runs from the initial transform/opacity.
    // eslint-disable-next-line no-unused-expressions
    notice.offsetHeight;
    notice.classList.add('is-visible');
}

function hideNotice(notice) {
    notice.classList.remove('is-visible');
    // Wait for the slide-out transition before fully hiding.
    setTimeout(() => { notice.hidden = true; }, 450);
}

window.addEventListener('DOMContentLoaded', () => {
    const url = new URL(window.location);
    const notice = document.querySelector('.four-notice');
    if (!notice) return;

    let fromVariant = url.searchParams.get('variant');
    let toVariant = url.searchParams.get('targetVariant');
    let originalUrl = url.searchParams.get('404');

    if (!isSafeVariant(toVariant)) {
        const stashed = readVariantSwitchFromStorage();
        if (stashed && isSafeVariant(stashed.targetVariant)) {
            fromVariant = stashed.fromVariant;
            toVariant = stashed.targetVariant;
            originalUrl = originalUrl || stashed.originalUrl;
        }
    }

    const isVariantSwitch = isSafeVariant(fromVariant) && isSafeVariant(toVariant);

    if (!originalUrl && !isVariantSwitch) return;

    // Resolve the missing pathname for display (strip protocol/host/query/fragment).
    let pathName = '';
    if (originalUrl) {
        try {
            const parsed = new URL(originalUrl, window.location.origin);
            if (['http:', 'https:'].includes(parsed.protocol)) {
                pathName = parsed.pathname;
            }
        } catch (e) { /* invalid; pathName stays empty */ }
    }

    // Title: "Page not found" by default; "Page not in <Variant>" on a
    // cross-variant switch (works for either direction).
    const titleEl = notice.querySelector('.four-notice-title');
    if (titleEl) {
        titleEl.textContent = isVariantSwitch
            ? `Page not in ${variantDisplayName(toVariant)}`
            : 'Page not found';
    }

    if (isVariantSwitch) clearVariantSwitchFromStorage();

    // Path line: hidden when we have no original URL; otherwise plain monospace
    // text, smart-truncated if it would overflow.
    const pathEl = notice.querySelector('.four-notice-path');
    if (pathEl) {
        if (pathName) {
            pathEl.hidden = false;
            pathEl.textContent = pathName;
        } else {
            pathEl.hidden = true;
        }
    }
    // Make the toast laid out (the slide-in is gated by .is-visible, set in
    // showNotice() below, so it's still off-screen at this point).
    notice.hidden = false;
    // Defer truncation to the next frame so layout has settled — important for
    // the close-button custom element (sl-icon) which can size oddly during
    // initial render.
    if (pathEl && pathName) {
        requestAnimationFrame(() => setPathWithSmartEllipsis(pathEl, pathName));
    }

    // Wire up the close button.
    const closeBtn = notice.querySelector('.four-notice-close');
    let dismissTimer = null;
    const dismiss = () => {
        if (dismissTimer) { clearTimeout(dismissTimer); dismissTimer = null; }
        hideNotice(notice);
    };
    if (closeBtn) closeBtn.addEventListener('click', dismiss);

    // Slide in, then auto-dismiss after the timeout.
    showNotice(notice);
    dismissTimer = setTimeout(dismiss, AUTO_DISMISS_MS);

    // Pause the auto-dismiss timer while the user is hovering the notice.
    notice.addEventListener('mouseenter', () => {
        if (dismissTimer) { clearTimeout(dismissTimer); dismissTimer = null; }
    });
    notice.addEventListener('mouseleave', () => {
        if (!dismissTimer) dismissTimer = setTimeout(dismiss, AUTO_DISMISS_MS);
    });
});
