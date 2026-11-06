const CACHE = "fomo-desk-v11";
const ASSETS = ["./", "./index.html", "./styles.css", "./app.js", "./manifest.webmanifest", "./icon-180.png", "./icon-192.png"];
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/d/") || url.pathname.startsWith("/demo-data/") || url.pathname.includes("/data/")) return;
  e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request)));
});
