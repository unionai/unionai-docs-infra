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
// Version switches work differently (DOC-1351). A variant switch knows in advance
// whether the page exists in the target (front-matter `variants`, surfaced as
// `data-allowed`), so it only signals when it knows the page is missing. Versions
// are SEPARATE BUILDS from different content refs, so nothing can know that at
// click time -- handleVersionChange stashes `versionSwitch` in sessionStorage on
// every switch (no query params, so a successful switch keeps a clean URL) and we
// show the notice only when `?404=` proves the walk-up ran.
//
// If targetVariant is set (via either source), show the cross-variant-switch
// banner; else if a fresh versionSwitch is stashed, show the cross-version one;
// otherwise show the default "we brought you to the closest page" banner. Both
// switch cases drop the "404" badge -- they are valid choices, not errors.

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

// Version segments are `latest`, a line (`v1`, `v2`), or a pinned tag
// (`v2.5.16.3`) -- see VERSIONING.md.
function isSafeVersion(v) {
    return typeof v === 'string' && /^[a-zA-Z0-9.-]{1,32}$/.test(v);
}

function versionDisplayName(seg) {
    if (!seg) return '';
    // "Page not in latest" reads as a typo; the others are already how the
    // version selector labels them.
    return seg === 'latest' ? 'the latest version' : seg;
}

function readVersionSwitchFromStorage() {
    try {
        const raw = sessionStorage.getItem('versionSwitch');
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object') return null;
        if (parsed.ts && Date.now() - parsed.ts > VARIANT_SWITCH_TTL_MS) {
            sessionStorage.removeItem('versionSwitch');
            return null;
        }
        return parsed;
    } catch (e) {
        return null;
    }
}

function clearVersionSwitchFromStorage() {
    try { sessionStorage.removeItem('versionSwitch'); } catch (e) { /* noop */ }
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

// Character budget for the path line in the toast. Tuned to the body grid
// column width (toast width minus badge, close button, gaps, and padding) for
// a monospace font at 0.78rem. Two breakpoints to match the CSS:
//   - desktop (>=600px viewport): toast is fixed at 22rem
//   - small (<600px): toast spans the viewport with 1rem margins
const PATH_MAX_CHARS = window.innerWidth < 600 ? 20 : 28;

// Abbreviate a path with ellipses to fit within `maxChars`, preferring natural
// separators. Tries each strategy in order; first candidate that fits wins:
//
//   Phase 1 — drop middle path segments (slash-delimited).  /docs/v2/…/page-name
//   Phase 2 — shrink the last segment by hyphen chunks.    /…/some-very-…-name
//   Phase 3 — character-level middle ellipsis fallback.
//
// In all phases, the last segment (or last chunk) is preserved intact so the
// most specific piece of the path remains readable.
function abbreviatePath(fullText, maxChars) {
    const E = '…';
    if (fullText.length <= maxChars) return fullText;

    const segments = fullText.split('/').filter(Boolean);
    const last = segments[segments.length - 1] || '';

    // Phase 1: drop middle path segments.
    if (segments.length >= 3) {
        for (let keepLeading = segments.length - 2; keepLeading >= 0; keepLeading--) {
            const prefix = keepLeading === 0
                ? `/${E}/`
                : `/${segments.slice(0, keepLeading).join('/')}/${E}/`;
            const candidate = prefix + last;
            if (candidate.length <= maxChars) return candidate;
        }
    }

    // Phase 2: shrink the last segment by hyphen chunks.
    const tailPrefix = segments.length >= 3 ? `/${E}/` : '/';
    const chunks = last.split('-');
    if (chunks.length >= 3) {
        const lastChunk = chunks[chunks.length - 1];
        for (let keepLeading = chunks.length - 2; keepLeading >= 0; keepLeading--) {
            const truncatedLast = keepLeading === 0
                ? `${E}-${lastChunk}`
                : `${chunks.slice(0, keepLeading).join('-')}-${E}-${lastChunk}`;
            const candidate = tailPrefix + truncatedLast;
            if (candidate.length <= maxChars) return candidate;
        }
    }

    // Phase 3: character-level middle ellipsis on the full path.
    if (maxChars <= 1) return E;
    const visible = maxChars - 1;
    const head = visible >> 1;
    const tail = visible - head;
    return fullText.slice(0, head) + E + fullText.slice(fullText.length - tail);
}

function showNotice(notice) {
    notice.hidden = false;
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

    // Version switches (DOC-1351). Unlike variants, the switcher cannot know in
    // advance whether the page exists in the target -- versions are separate
    // builds -- so it stashes on EVERY switch and we gate on `?404=`, which only
    // appears when the walk-up actually ran. Storage-only, so a successful switch
    // keeps a clean URL.
    let toVersion = '';
    if (originalUrl) {
        const stashedVersion = readVersionSwitchFromStorage();
        if (stashedVersion && isSafeVersion(stashedVersion.targetVersion)) {
            toVersion = stashedVersion.targetVersion;
        }
    }
    const isVersionSwitch = !isVariantSwitch && !!toVersion;

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
    // cross-variant switch and "Page not in <version>" on a cross-version one
    // (both work in either direction).
    const titleEl = notice.querySelector('.four-notice-title');
    if (titleEl) {
        if (isVariantSwitch) {
            titleEl.textContent = `Page not in ${variantDisplayName(toVariant)}`;
        } else if (isVersionSwitch) {
            titleEl.textContent = `Page not in ${versionDisplayName(toVersion)}`;
        } else {
            titleEl.textContent = 'Page not found';
        }
    }

    // Drop the "404" badge when the reader got here by switching variant or
    // version (DOC-1351). Those are deliberate, valid choices from a menu we
    // rendered, so a miss is our limitation, not their mistake -- an error code
    // frames it as the latter. A genuinely dead link still gets the badge.
    // `is-switch` collapses the toast grid from `auto 1fr auto` to `1fr auto`.
    const isSwitch = isVariantSwitch || isVersionSwitch;
    const badgeEl = notice.querySelector('.four-notice-badge');
    if (badgeEl && isSwitch) badgeEl.hidden = true;
    if (isSwitch) notice.classList.add('is-switch');

    if (isVariantSwitch) clearVariantSwitchFromStorage();
    if (isVersionSwitch) clearVersionSwitchFromStorage();

    // Path line: hidden when we have no original URL; otherwise plain monospace
    // text, abbreviated to PATH_MAX_CHARS via natural separators.
    const pathEl = notice.querySelector('.four-notice-path');
    if (pathEl) {
        if (pathName) {
            pathEl.hidden = false;
            pathEl.textContent = abbreviatePath(pathName, PATH_MAX_CHARS);
        } else {
            pathEl.hidden = true;
        }
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
