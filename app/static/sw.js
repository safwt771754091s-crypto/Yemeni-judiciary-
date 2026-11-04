/*
 * Service Worker — المنصة القضائية القانونية اليمنية
 *
 * Strategy
 *   - App shell (HTML pages, icons, manifest): cache-first with a background
 *     refresh, so the portal opens instantly and works with no connection.
 *   - API calls (/api/...): network-only. They are authenticated and per-user;
 *     caching them would risk serving one user's data to another, so they are
 *     never written to the cache. When offline the UI shows an offline notice.
 *   - Navigation to an uncached page: fall back to the offline page.
 *
 * Bump CACHE_VERSION whenever the shell changes to retire the old cache.
 */
const CACHE_VERSION = "yemen-legal-v3";
const SHELL_CACHE = `${CACHE_VERSION}-shell`;
const RUNTIME_CACHE = `${CACHE_VERSION}-runtime`;

// Core pages and assets that make up the installable app shell.
const APP_SHELL = [
  "/",
  "/manifest.json",
  "/ui/landing.html",
  "/ui/index.html",
  "/ui/admin.html",
  "/ui/judge.html",
  "/ui/lawyer.html",
  "/ui/prosecution.html",
  "/ui/researcher.html",
  "/ui/assistant.html",
  "/ui/workspace.html",
  "/ui/install.html",
  "/ui/offline.html",
  "/ui/install.js",
  "/ui/portal.css",
  "/ui/portal.js",
  "/ui/portal-app.js",
  "/ui/workspace.css",
  "/ui/workspace.js",
  "/ui/icons/icon-192.png",
  "/ui/icons/icon-512.png",
  "/ui/icons/icon-maskable-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      // addAll fails the whole install if any single entry 404s, so add each
      // entry independently and tolerate the ones that are missing.
      .then((cache) =>
        Promise.all(
          APP_SHELL.map((url) =>
            cache.add(url).catch(() => undefined)
          )
        )
      )
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => !key.startsWith(CACHE_VERSION))
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);

  // Never intercept cross-origin requests.
  if (url.origin !== self.location.origin) return;

  // Authenticated API traffic is always live: no caching, and a clean JSON
  // error when the network is unavailable rather than a stale body.
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(request).catch(
        () =>
          new Response(
            JSON.stringify({
              error: {
                code: "offline",
                message: "لا يوجد اتصال بالإنترنت. تعذّر تنفيذ الطلب.",
              },
            }),
            {
              status: 503,
              headers: { "Content-Type": "application/json; charset=utf-8" },
            }
          )
      )
    );
    return;
  }

  // Page navigations: try the network, fall back to the cached shell page,
  // then to the offline page.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(RUNTIME_CACHE).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() =>
          caches
            .match(request)
            .then(
              (cached) =>
                cached ||
                caches.match("/ui/offline.html") ||
                caches.match("/")
            )
        )
    );
    return;
  }

  // Static assets: cache-first, refresh in the background.
  event.respondWith(
    caches.match(request).then((cached) => {
      const network = fetch(request)
        .then((response) => {
          if (response && response.status === 200) {
            const copy = response.clone();
            caches.open(RUNTIME_CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});

// Allow the page to trigger an immediate update.
self.addEventListener("message", (event) => {
  if (event.data === "SKIP_WAITING") self.skipWaiting();
});
