/*
 * image-lightbox.js — click-to-zoom for content images (DOC-1249)
 *
 * Sizeable images in `.content` (diagrams, screenshots) open in a modal on click.
 * Inside the modal there are two zoom levels:
 *   - fit    (default): whole image visible, bounded by 96vw / 96vh
 *   - full   (click the image): width = 96vw, height auto, scroll to see it all
 *     — useful for tall/square diagrams that stay small when height-bound on a laptop.
 * Close: Esc, click the backdrop (outside the image), or the × button.
 * Tiny inline icons (naturalWidth < 300) and already-linked images are skipped.
 */
(function () {
  "use strict";

  var MIN_NATURAL_WIDTH = 300; // below this = an inline icon/badge, not worth zooming
  var overlay = null;
  var overlayImg = null;

  function buildOverlay() {
    if (overlay) return;

    overlay = document.createElement("div");
    overlay.className = "img-lightbox";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-hidden", "true");

    overlayImg = document.createElement("img");
    overlayImg.className = "img-lightbox__img";
    overlayImg.alt = "";
    // Click the image → toggle fit / full-width zoom (does NOT close).
    overlayImg.addEventListener("click", function (e) {
      e.stopPropagation();
      overlay.classList.toggle("is-zoomed");
      overlay.scrollTop = 0;
    });

    var closeBtn = document.createElement("button");
    closeBtn.className = "img-lightbox__close";
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "Close");
    closeBtn.innerHTML = "&times;";
    closeBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      close();
    });

    overlay.appendChild(overlayImg);
    overlay.appendChild(closeBtn);
    // Click the backdrop (anywhere but the image / button) → close.
    overlay.addEventListener("click", close);
    document.body.appendChild(overlay);
  }

  function open(src, alt) {
    buildOverlay();
    overlay.classList.remove("is-zoomed"); // always start in fit mode
    overlayImg.src = src;
    overlayImg.alt = alt || "";
    overlay.classList.add("is-open");
    overlay.setAttribute("aria-hidden", "false");
    document.body.classList.add("img-lightbox-open");
  }

  function close() {
    if (!overlay) return;
    overlay.classList.remove("is-open", "is-zoomed");
    overlay.setAttribute("aria-hidden", "true");
    document.body.classList.remove("img-lightbox-open");
    overlayImg.removeAttribute("src"); // release the (possibly large) image
  }

  function makeZoomable(img) {
    if (img.dataset.lightbox === "done") return;
    if (img.closest("a")) return; // skip images that are already links
    if ((img.naturalWidth || 0) < MIN_NATURAL_WIDTH) return;
    img.dataset.lightbox = "done";
    img.classList.add("is-zoomable");
    img.addEventListener("click", function () {
      open(img.currentSrc || img.src, img.alt);
    });
  }

  function init() {
    var imgs = document.querySelectorAll(".content img");
    for (var i = 0; i < imgs.length; i++) {
      var img = imgs[i];
      if (img.complete && img.naturalWidth) {
        makeZoomable(img);
      } else {
        (function (el) {
          el.addEventListener("load", function () { makeZoomable(el); }, { once: true });
        })(img);
      }
    }
  }

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") close();
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
