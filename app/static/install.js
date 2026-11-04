/* ==========================================================================
   المنصة القضائية القانونية اليمنية — تثبيت التطبيق (PWA install)
   Single source of truth for "install / add to home screen" on every page.

   It registers the service worker, captures the browser's one-shot
   ``beforeinstallprompt`` event as early as possible, and exposes
   ``window.PWAInstall`` so dynamically-rendered pages (the role portals) can
   wire their own install button after they build their DOM.
   ========================================================================== */
(function () {
  "use strict";

  var deferredPrompt = null;

  function isInstalled() {
    try {
      return (
        window.matchMedia("(display-mode: standalone)").matches ||
        window.matchMedia("(display-mode: fullscreen)").matches ||
        window.navigator.standalone === true
      );
    } catch (e) {
      return false;
    }
  }

  function platform() {
    var ua = navigator.userAgent || "";
    if (/android/i.test(ua)) return "android";
    if (/iphone|ipad|ipod/i.test(ua)) return "ios";
    return "desktop";
  }

  function canPrompt() {
    return Boolean(deferredPrompt);
  }

  async function promptInstall() {
    if (!deferredPrompt) return null;
    deferredPrompt.prompt();
    var choice = await deferredPrompt.userChoice;
    deferredPrompt = null;
    document.dispatchEvent(new CustomEvent("pwa:prompted", { detail: choice }));
    return choice;
  }

  function steps() {
    var p = platform();
    if (p === "android") {
      return [
        "افتح قائمة المتصفح (زر النقاط الثلاث في أعلى الشاشة).",
        "اختر «تثبيت التطبيق» أو «إضافة إلى الشاشة الرئيسية».",
        "أكّد بالضغط على «تثبيت».",
      ];
    }
    if (p === "ios") {
      return [
        "افتح زر المشاركة في متصفح Safari (المربّع مع السهم).",
        "اختر «إضافة إلى الشاشة الرئيسية».",
        "أكّد بالضغط على «إضافة».",
      ];
    }
    return [
      "افتح قائمة المتصفح.",
      "اختر «تثبيت» أو «إضافة إلى الشاشة الرئيسية» إن كانت متاحة.",
      "للتثبيت على أندرويد، استخدم متصفح Chrome للحصول على تجربة كاملة.",
    ];
  }

  function showHelp() {
    injectStyles();
    var overlay = document.createElement("div");
    overlay.className = "install-modal";
    var list = steps()
      .map(function (s) {
        return "<li>" + escapeHtml(s) + "</li>";
      })
      .join("");
    overlay.innerHTML =
      '<div class="im-box">' +
      "<h3>تثبيت التطبيق على جهازك</h3>" +
      '<p class="muted">اتبع الخطوات التالية لإضافة المنصة إلى الشاشة الرئيسية:</p>' +
      "<ol>" +
      list +
      "</ol>" +
      '<div class="actions"><button type="button" class="im-close">حسناً</button></div>' +
      "</div>";
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay || event.target.classList.contains("im-close")) {
        overlay.remove();
      }
    });
    document.body.appendChild(overlay);
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  // Wire a button so it installs via the native prompt when available, and
  // otherwise opens the platform-specific "add to home screen" steps.
  function wireButton(selector) {
    var button = document.querySelector(selector);
    if (!button || button.__pwaWired) return;
    button.__pwaWired = true;

    // Visibility is driven by inline style as well as the .hide class, so the
    // button also works on pages that do not load the shared stylesheet.
    function show() {
      button.classList.remove("hide");
      button.style.display = "";
    }
    function hide() {
      button.classList.add("hide");
      button.style.display = "none";
    }
    if (isInstalled()) {
      hide();
      return;
    }
    var refresh = function () {
      if (isInstalled()) hide();
      else if (canPrompt() || platform() !== "desktop") show();
    };
    refresh();
    document.addEventListener("pwa:installable", refresh);
    document.addEventListener("pwa:installed", hide);

    button.addEventListener("click", async function () {
      if (canPrompt()) {
        var choice = await promptInstall();
        if (choice && choice.outcome === "accepted") {
          hide();
          toast("تم تثبيت التطبيق. افتحه من الشاشة الرئيسية.");
        }
        return;
      }
      showHelp();
    });
  }

  function toast(message) {
    var existing = document.querySelector(".toast");
    if (existing) existing.remove();
    var node = document.createElement("div");
    node.className = "toast";
    node.textContent = message;
    document.body.appendChild(node);
    setTimeout(function () {
      node.remove();
    }, 4200);
  }

  function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () {});
    });
  }

  var STYLES =
    ".install-modal{position:fixed;inset:0;z-index:80;display:flex;align-items:center;" +
    "justify-content:center;background:rgba(6,10,14,.72);padding:22px}" +
    ".install-modal .im-box{width:100%;max-width:460px;background:#17212b;border:1px solid #2c3a49;" +
    "border-radius:16px;padding:24px;box-shadow:0 20px 60px rgba(0,0,0,.5);color:#e8eef5;" +
    "font-family:'Segoe UI','Noto Naskh Arabic',Tahoma,sans-serif;line-height:1.75}" +
    ".install-modal h3{margin:0 0 6px;color:#e0be6a;font-size:17px}" +
    ".install-modal .muted{color:#93a4b6;font-size:13px}" +
    ".install-modal ol{margin:12px 0 4px;padding-inline-start:22px;font-size:14px;line-height:2}" +
    ".install-modal .actions{display:flex;justify-content:flex-end;margin-top:14px}" +
    ".install-modal .im-close{background:linear-gradient(135deg,#c8a24a,#a8823a);color:#12181f;" +
    "border:0;border-radius:9px;padding:10px 22px;font-size:14px;font-weight:700;cursor:pointer;" +
    "font-family:inherit}" +
    ".toast{position:fixed;inset-inline-start:20px;bottom:20px;z-index:90;background:#22303f;" +
    "border:1px solid #c8a24a;color:#e8eef5;border-radius:10px;padding:12px 18px;font-size:13.5px;" +
    "max-width:380px;box-shadow:0 8px 30px rgba(0,0,0,.45)}";

  function injectStyles() {
    if (document.getElementById("pwa-install-css")) return;
    var style = document.createElement("style");
    style.id = "pwa-install-css";
    style.textContent = STYLES;
    (document.head || document.documentElement).appendChild(style);
  }

  // Capture installability before any page script runs — the event fires once.
  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    deferredPrompt = event;
    document.dispatchEvent(new CustomEvent("pwa:installable"));
  });
  window.addEventListener("appinstalled", function () {
    deferredPrompt = null;
    document.dispatchEvent(new CustomEvent("pwa:installed"));
  });

  window.PWAInstall = {
    isInstalled: isInstalled,
    platform: platform,
    canPrompt: canPrompt,
    promptInstall: promptInstall,
    showHelp: showHelp,
    wireButton: wireButton,
    register: registerServiceWorker,
  };

  registerServiceWorker();
  injectStyles();

  // Auto-wire a static install button on pages that do not render their DOM
  // dynamically (index, admin, intake). Role portals call wireButton() after
  // they build their shell, so this is a no-op there.
  document.addEventListener("DOMContentLoaded", function () {
    var button = document.querySelector("#installBtn");
    if (button && !button.__pwaWired) wireButton("#installBtn");
  });
})();
