const CACHE = 'ipad-drop-v1';
const BASE = new URL(self.registration.scope).pathname.replace(/\/$/, '');
const ASSETS = [
  `${BASE}/`,
  `${BASE}/static/style.css`,
  `${BASE}/static/app.js`,
  `${BASE}/static/icon-192.png`,
  `${BASE}/static/icon-512.png`
];
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS))));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
});
