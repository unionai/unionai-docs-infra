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

    if (!originalUrl && !isVariantSwitch) return;

    if (originalUrl) {
        let parsedOriginal = null;
        try {
            parsedOriginal = new URL(originalUrl, window.location.origin);
        } catch (e) { /* invalid */ }
        if (parsedOriginal && ['http:', 'https:'].includes(parsedOriginal.protocol)) {
            // Strip query string and fragment for display — internal walk-up
            // params (?404=, ?source=, etc.) are noise to the user.
            const cleanUrl = parsedOriginal.origin + parsedOriginal.pathname;
            notice.querySelectorAll('.four-page').forEach((el) => {
                el.replaceChildren();
                const link = document.createElement('a');
                link.href = cleanUrl;
                link.textContent = cleanUrl;
                el.appendChild(link);
            });
        }
    }

    if (isVariantSwitch) {
        const fromName = variantDisplayName(fromVariant);
        const toName = variantDisplayName(toVariant);
        notice.querySelectorAll('.four-from-variant').forEach((el) => {
            el.textContent = fromName;
        });
        notice.querySelectorAll('.four-to-variant, .four-to-variant-2, .four-to-variant-3').forEach((el) => {
            el.textContent = toName;
        });
        const variantBanner = notice.querySelector('.four-notice-variant-switch');
        const defaultBanner = notice.querySelector('.four-notice-default');
        if (variantBanner) variantBanner.hidden = false;
        if (defaultBanner) defaultBanner.hidden = true;

        clearVariantSwitchFromStorage();
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
