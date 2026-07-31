(function () {
  if (!window.URL || typeof window.URL.createObjectURL !== "function") {
    return;
  }

  var originalCreateObjectURL = window.URL.createObjectURL.bind(window.URL);
  // RunLLM previews and opens attachments via same-origin blob URLs.
  var blockedMimeTypes = {
    "application/xhtml+xml": true,
    "application/xml": true,
    "image/svg+xml": true,
    "text/html": true,
    "text/xml": true
  };
  var blockedExtensions = /\.(htm|html|mht|mhtml|svg|xhtml|xml)$/i;

  function isBlockedAttachment(object) {
    if (!(object instanceof Blob)) {
      return false;
    }

    var type = (object.type || "").split(";")[0].trim().toLowerCase();
    if (blockedMimeTypes[type]) {
      return true;
    }

    return typeof object.name === "string" && blockedExtensions.test(object.name);
  }

  window.URL.createObjectURL = function (object) {
    if (isBlockedAttachment(object)) {
      return originalCreateObjectURL(
        new Blob(["This attachment type cannot be previewed on union.ai."], {
          type: "text/plain"
        })
      );
    }

    return originalCreateObjectURL(object);
  };
})();
