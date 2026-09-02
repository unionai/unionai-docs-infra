/*
 * The page-actions menu: Claude, ChatGPT, copy, view as markdown.
 *
 * All four are the same fact in different clothes -- this page has a `.md`
 * twin, and the twin is self-describing. The AI actions hand a model the twin's
 * URL rather than the HTML page, because the twin opens with the identity block
 * naming the product, the version line and the index (DOC-1509).
 *
 * "Copy page" copies the twin's TEXT, not its URL. Someone copying a page wants
 * to paste it somewhere that cannot fetch, which is the whole point.
 */
(function () {
  "use strict";

  var PROMPT = function (url) {
    return "Read " + url + " so I can ask questions about it.";
  };

  function openAI(base, mdUrl) {
    window.open(base + encodeURIComponent(PROMPT(mdUrl)), "_blank", "noopener");
  }

  // Feedback goes on the PRIMARY BUTTON, not the menu item. Selecting an item
  // closes the menu, so a label that changes in there changes out of sight --
  // which is how the first version of this shipped and why it looked like the
  // copy had silently done nothing.
  function flash(root, message) {
    var label = root.querySelector(".page-actions-primary span");
    if (!label) return;
    var original = label.textContent;
    label.textContent = message;
    setTimeout(function () { label.textContent = original; }, 1800);
  }

  async function copyPage(mdUrl, root) {
    try {
      var res = await fetch(mdUrl, { credentials: "omit" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      await navigator.clipboard.writeText(await res.text());
      flash(root, "Copied");
    } catch (err) {
      // Clipboard access is refused outright in some contexts (no user
      // gesture, insecure origin, permissions policy). Say so rather than
      // leaving the reader to discover it.
      flash(root, "Copy failed");
      if (window.console) console.warn("[page-actions] copy failed:", err);
    }
  }

  // Delegated on `document`, deliberately. Binding straight to the <sl-menu>
  // did not work: the element is parsed before Shoelace defines it, the
  // autoloader upgrades it afterwards, and `hoist` re-parents the panel out to
  // the body when it opens. A listener attached at DOMContentLoaded ends up on
  // the wrong object. `sl-select` bubbles and is composed, so document sees it
  // wherever the panel has been moved to.
  function onSelect(event) {
    var menu = event.target;
    if (!menu || menu.tagName !== "SL-MENU") return;
    if (!menu.classList.contains("page-actions-menu")) return;

    var root = document.querySelector(".page-actions");
    if (!root) return;
    var mdUrl = root.getAttribute("data-md-url");         // same-origin
    var mdAbs = root.getAttribute("data-md-abs") || mdUrl; // handed to a model
    if (!mdUrl) return;

    var item = event.detail && event.detail.item;
    if (!item) return;

    switch (item.value) {
      case "claude":
        openAI("https://claude.ai/new?q=", mdAbs);
        break;
      case "chatgpt":
        openAI("https://chatgpt.com/?q=", mdAbs);
        break;
      case "copy":
        copyPage(mdUrl, root);
        break;
      case "markdown":
        window.location.href = mdUrl;
        break;
    }
  }

  // Bind only once <sl-menu> is DEFINED. Shoelace is loaded by its autoloader,
  // so at script time the element is still an unknown tag; a listener registered
  // before that never receives sl-select, while an identical one registered
  // afterwards does. whenDefined is the documented way to wait for it.
  if (window.customElements && customElements.whenDefined) {
    customElements.whenDefined("sl-menu").then(function () {
      document.addEventListener("sl-select", onSelect);
    });
  } else {
    document.addEventListener("sl-select", onSelect);
  }
})();
