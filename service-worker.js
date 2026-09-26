const CACHE_NAME = "rsp-tender-shell-v6";
const APP_SHELL = ["./", "./index.html", "./manifest.webmanifest", "./assets/rsp-logo.png", "./assets/rsp-icon-192.png", "./assets/rsp-icon-512.png", "./assets/whatsapp.svg"];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET" || new URL(event.request.url).origin !== self.location.origin) return;
  event.respondWith(
    fetch(event.request)
      .then(response => {
        if (response.ok && new URL(event.request.url).origin === self.location.origin) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request).then(response => response || caches.match("./index.html")))
  );
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = new URL((event.notification.data && event.notification.data.url) || "./", self.registration.scope).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(list => {
      const client = list.find(c => new URL(c.url).origin === self.location.origin);
      if (client) {
        client.postMessage({ type: "rsp-open", url });
        return client.focus();
      }
      return self.clients.openWindow(url);
    })
  );
});

self.addEventListener("push", event => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch (e) { d = { body: event.data ? event.data.text() : "" }; }
  event.waitUntil(
    self.registration.showNotification(d.title || "RSP Tender Monitor", {
      body: d.body || "",
      icon: "assets/rsp-icon-192.png",
      badge: "assets/rsp-icon-192.png",
      tag: d.tag || "rsp-tender",
      renotify: true,
      data: { url: d.url || "./" }
    })
  );
});
