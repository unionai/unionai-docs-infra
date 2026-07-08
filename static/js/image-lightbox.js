/*
 * image-lightbox.js — click-to-zoom for content images (DOC-1249)
 *
 * Sizeable images in `.content` (diagrams, screenshots) open in a full-viewport
 * modal on click so they can be read at large size. Tiny inline icons and images
 * that are already links are skipped. Click anywhere or press Esc to close.
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
    overlay.appendChild(overlayImg);
    overlay.addEventListener("click", close);
    document.body.appendChild(overlay);
  }

  function open(src, alt) {
    buildOverlay();
    overlayImg.src = src;
    overlayImg.alt = alt || "";
    overlay.classList.add("is-open");
    overlay.setAttribute("aria-hidden", "false");
    document.body.classList.add("img-lightbox-open");
  }

  function close() {
    if (!overlay) return;
    overlay.classList.remove("is-open");
    overlay.setAttribute("aria-hidden", "true");
    document.body.classList.remove("img-lightbox-open");
    // release the (possibly large) image once hidden
    overlayImg.removeAttribute("src");
  }

  function makeZoomable(img) {
    if (img.dataset.lightbox === "done") return;
    // skip images that are already interactive links
    if (img.closest("a")) return;
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
