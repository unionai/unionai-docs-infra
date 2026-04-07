// Display a banner when the user lands on a page after a 404 walk-up.
//
// The walk-up handler in four-o-four.html sets these query params on the
// landed-on page:
//   ?404=<original URL>            — always set
//   ?source=<original page title>  — only on cross-variant switches
//   ?variant=<from variant>        — only on cross-variant switches
//   ?targetVariant=<to variant>    — only on cross-variant switches
//
// If targetVariant is set, show the cross-variant-switch banner. Otherwise
// show the default "we brought you to the closest page" banner.

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

window.addEventListener('DOMContentLoaded', () => {
    const url = new URL(window.location);
    const fourOFourMessage = document.querySelector('.four-notice');
    if (!fourOFourMessage || url.searchParams.get('404') === null) return;

    fourOFourMessage.style.display = 'block';

    // Render a link to the original page in all .four-page elements.
    const originalUrl = url.searchParams.get('404');
    let originalIsHttp = false;
    try {
        originalIsHttp = ['http:', 'https:'].includes(new URL(originalUrl).protocol);
    } catch (e) {
        originalIsHttp = false;
    }
    if (originalIsHttp) {
        document.querySelectorAll('.four-page').forEach((el) => {
            el.replaceChildren();
            const link = document.createElement('a');
            link.href = originalUrl;
            link.textContent = originalUrl;
            el.appendChild(link);
        });
    }

    // Cross-variant switch case: targetVariant is set by the variant selector.
    const fromVariant = url.searchParams.get('variant');
    const toVariant = url.searchParams.get('targetVariant');
    if (isSafeVariant(fromVariant) && isSafeVariant(toVariant)) {
        const fromName = variantDisplayName(fromVariant);
        const toName = variantDisplayName(toVariant);
        document.querySelectorAll('.four-from-variant').forEach((el) => {
            el.textContent = fromName;
        });
        document.querySelectorAll('.four-to-variant, .four-to-variant-2, .four-to-variant-3').forEach((el) => {
            el.textContent = toName;
        });
        const variantBanner = document.querySelector('.four-notice-variant-switch');
        const defaultBanner = document.querySelector('.four-notice-default');
        if (variantBanner) variantBanner.style.display = 'inline';
        if (defaultBanner) defaultBanner.style.display = 'none';
    }
});
