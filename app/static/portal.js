/* ==========================================================================
   المنصة القضائية القانونية اليمنية — طبقة التطبيق المشتركة لبوابات الأدوار
   Shared client runtime: auth, API, PWA registration, tiny markdown renderer
   and a toast. Every portal page includes this before its own script.
   ========================================================================== */
(function (global) {
  "use strict";

  const TOKEN_KEY = "legal_portal_token";

  const api = {
    token() {
      return sessionStorage.getItem(TOKEN_KEY) || "";
    },
    setToken(token) {
      if (token) sessionStorage.setItem(TOKEN_KEY, token);
      else sessionStorage.removeItem(TOKEN_KEY);
    },
    headers(extra) {
      const h = Object.assign({ "Content-Type": "application/json" }, extra || {});
      const t = api.token();
      if (t) h.Authorization = "Bearer " + t;
      return h;
    },
    async call(path, opts) {
      opts = opts || {};
      const response = await fetch("/api/v1" + path, {
        method: opts.method || "GET",
        headers: api.headers(opts.headers),
        body: opts.body ? JSON.stringify(opts.body) : undefined,
      });
      let payload = null;
      const text = await response.text();
      if (text) {
        try { payload = JSON.parse(text); } catch (e) { payload = { raw: text }; }
      }
      if (!response.ok) {
        const message =
          (payload && payload.error && payload.error.message) ||
          (payload && payload.detail) ||
          "تعذّر تنفيذ الطلب (" + response.status + ")";
        const err = new Error(
          typeof message === "string" ? message : JSON.stringify(message)
        );
        err.status = response.status;
        err.payload = payload;
        throw err;
      }
      return payload;
    },
    get: (p) => api.call(p),
    post: (p, body) => api.call(p, { method: "POST", body }),
    patch: (p, body) => api.call(p, { method: "PATCH", body }),
    del: (p) => api.call(p, { method: "DELETE" }),
  };

  const auth = {
    async login(username, password) {
      const data = await api.call("/auth/login", {
        method: "POST",
        body: { username, password },
      });
      api.setToken(data.access_token);
      return data;
    },
    async signup(payload) {
      const data = await api.call("/auth/signup", {
        method: "POST",
        body: payload,
      });
      api.setToken(data.access_token);
      return data;
    },
    async signupOptions() {
      return api.get("/auth/signup");
    },
    logout() {
      api.setToken("");
      location.href = "/ui/index.html";
    },
    async me() {
      return api.get("/auth/me");
    },
    async requireSession() {
      if (!api.token()) {
        location.href = "/ui/index.html";
        return null;
      }
      try {
        return await auth.me();
      } catch (e) {
        api.setToken("");
        location.href = "/ui/index.html";
        return null;
      }
    },
    has(user, permission) {
      if (!user) return false;
      const perms = user.effective_permissions || user.permissions || [];
      return user.role === "owner" || perms.indexOf(permission) !== -1;
    },
  };

  // The install/PWA logic lives in install.js (loaded before this file) so a
  // single implementation serves the static pages and the role portals alike.
  const pwa = {
    get ready() { return Boolean(window.PWAInstall); },
    register() { if (window.PWAInstall) window.PWAInstall.register(); },
    installed() { return window.PWAInstall ? window.PWAInstall.isInstalled() : false; },
    platform() { return window.PWAInstall ? window.PWAInstall.platform() : "desktop"; },
    canPrompt() { return window.PWAInstall ? window.PWAInstall.canPrompt() : false; },
    promptInstall() {
      return window.PWAInstall ? window.PWAInstall.promptInstall() : Promise.resolve(null);
    },
    wireInstallButton(selector) {
      if (window.PWAInstall) window.PWAInstall.wireButton(selector);
    },
    showInstallHelp() { if (window.PWAInstall) window.PWAInstall.showHelp(); },
  };

  // -- a deliberately tiny markdown renderer -----------------------------
  // Enough for the documents the engine emits (headings, lists, bold, quotes,
  // rules, paragraphs). It escapes HTML first, so model output cannot inject.
  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }
  function renderMarkdown(md) {
    if (!md) return "";
    const lines = escapeHtml(md).split(/\r?\n/);
    let html = "";
    let inList = false;
    let inQuote = false;
    const closeList = () => { if (inList) { html += "</ul>"; inList = false; } };
    const closeQuote = () => { if (inQuote) { html += "</blockquote>"; inQuote = false; } };
    for (const raw of lines) {
      const line = raw.trimEnd();
      if (!line.trim()) { closeList(); closeQuote(); continue; }
      let m;
      if ((m = line.match(/^(#{1,3})\s+(.*)$/))) {
        closeList(); closeQuote();
        html += "<h" + m[1].length + ">" + m[2] + "</h" + m[1].length + ">";
      } else if (/^(-{3,}|\*{3,})$/.test(line.trim())) {
        closeList(); closeQuote(); html += "<hr>";
      } else if ((m = line.match(/^[-*]\s+(.*)$/))) {
        closeQuote();
        if (!inList) { html += "<ul>"; inList = true; }
        html += "<li>" + m[1] + "</li>";
      } else if ((m = line.match(/^>\s?(.*)$/))) {
        closeList();
        if (!inQuote) { html += "<blockquote>"; inQuote = true; }
        html += m[1] + "<br>";
      } else {
        closeList(); closeQuote();
        html += "<p>" + line + "</p>";
      }
    }
    closeList(); closeQuote();
    return html
      .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
      .replace(/`(.+?)`/g, "<code>$1</code>");
  }

  function toast(message, kind) {
    const existing = document.querySelector(".toast");
    if (existing) existing.remove();
    const node = document.createElement("div");
    node.className = "toast";
    if (kind === "err") node.style.borderColor = "var(--err)";
    node.textContent = message;
    document.body.appendChild(node);
    setTimeout(() => node.remove(), 4200);
  }

  function el(id) { return document.getElementById(id); }
  function esc(text) { return escapeHtml(text == null ? "" : text); }
  function fmtDate(value) {
    if (!value) return "—";
    try {
      return new Date(value).toLocaleString("ar", {
        dateStyle: "medium", timeStyle: "short",
      });
    } catch (e) { return String(value); }
  }

  global.Portal = {
    api, auth, pwa, renderMarkdown, toast, el, esc, fmtDate, escapeHtml,
  };
})(window);
