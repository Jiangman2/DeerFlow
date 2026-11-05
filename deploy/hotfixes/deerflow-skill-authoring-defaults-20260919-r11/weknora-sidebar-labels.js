(function () {
  "use strict";

  var navigationLabels = {
    "nav-creatChat": {
      "New Chat": "\u77e5\u8bc6\u95ee\u7b54",
      "\u65b0\u5bf9\u8bdd": "\u77e5\u8bc6\u95ee\u7b54",
    },
    "nav-agents": {
      Agents: "\u81ea\u5b9a\u4e49\u95ee\u7b54",
      "\u667a\u80fd\u4f53": "\u81ea\u5b9a\u4e49\u95ee\u7b54",
    },
  };

  function renameExactValue(value, replacements) {
    if (typeof value !== "string") return value;
    var trimmed = value.trim();
    if (!Object.prototype.hasOwnProperty.call(replacements, trimmed)) {
      return value;
    }
    return value.replace(trimmed, replacements[trimmed]);
  }

  function renameNavigationItem(item, replacements) {
    var walker = document.createTreeWalker(item, NodeFilter.SHOW_TEXT);
    var node;
    while ((node = walker.nextNode())) {
      var next = renameExactValue(node.nodeValue, replacements);
      if (next !== node.nodeValue) node.nodeValue = next;
    }
    ["aria-label", "title"].forEach(function (attribute) {
      if (!item.hasAttribute(attribute)) return;
      var current = item.getAttribute(attribute);
      var next = renameExactValue(current, replacements);
      if (next !== current) item.setAttribute(attribute, next);
    });
  }

  function applyNavigationLabels() {
    Object.keys(navigationLabels).forEach(function (guide) {
      document
        .querySelectorAll('[data-guide="' + guide + '"]')
        .forEach(function (item) {
          renameNavigationItem(item, navigationLabels[guide]);
        });
    });
  }

  var pending = false;
  function scheduleNavigationLabels() {
    if (pending) return;
    pending = true;
    queueMicrotask(function () {
      pending = false;
      applyNavigationLabels();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scheduleNavigationLabels, {
      once: true,
    });
  } else {
    scheduleNavigationLabels();
  }

  new MutationObserver(scheduleNavigationLabels).observe(
    document.documentElement,
    {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ["aria-label", "title"],
    },
  );
})();
