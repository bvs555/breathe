// Breathe: offline copy of the page and its icons.
// Cache-first, refreshed in the background: a launch is instant and works with
// no network; a newer version is picked up on the launch after it is published.
// Bump CACHE whenever any of the files below changes.
const CACHE = 'breathe-v2';
const FILES = [
  './',
  './index.html',
  './manifest.webmanifest',
  './apple-touch-icon.png',
  './icon-192.png',
  './icon-512.png',
  './icon-maskable-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  event.respondWith(
    caches.open(CACHE).then((cache) =>
      cache.match(event.request, { ignoreSearch: true }).then((cached) => {
        const refresh = fetch(event.request)
          .then((resp) => {
            if (resp && resp.ok && new URL(event.request.url).origin === self.location.origin) {
              cache.put(event.request, resp.clone());
            }
            return resp;
          })
          .catch(() => cached);
        return cached || refresh;
      })
    )
  );
});
