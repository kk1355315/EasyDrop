const BASE = new URL(self.registration.scope).pathname.replace(/\/$/, '');
const SHARED = BASE.endsWith('-pc') ? BASE.slice(0, -3) : BASE;
const CACHE = `easydrop-v3:${BASE}`;
const ASSETS = [
  `${BASE}/`,
  `${SHARED}/static/style.css`,
  `${SHARED}/static/targets.css`,
  `${SHARED}/static/app.js`,
  `${SHARED}/static/install.js`,
  `${SHARED}/static/icon-192.png`,
  `${SHARED}/static/icon-512.png`
];
self.addEventListener('install', event => event.waitUntil((async () => {
  const cache = await caches.open(CACHE);
  await cache.addAll(ASSETS);
  await self.skipWaiting();
})()));
self.addEventListener('activate', event => event.waitUntil((async () => {
  for (const name of await caches.keys()) {
    if (name === 'ipad-drop-v1' || name === 'easydrop-v2') await caches.delete(name);
  }
  await self.clients.claim();
})()));
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
});
