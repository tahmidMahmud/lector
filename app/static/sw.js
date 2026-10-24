// Lector service worker: caches the static app shell only.
// Never caches /api (data, covers, audio) — those always go to the server.
const CACHE = 'lector-shell-v1';
const SHELL = [
  '/',
  '/static/app.css',
  '/static/app.js',
  '/static/icons/icon.svg',
  '/manifest.webmanifest',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api')) return;           // never cache API / audio / covers
  if (req.headers.has('range')) return;

  const isNav = req.mode === 'navigate';
  const isShell = isNav || url.pathname.startsWith('/static/') ||
    url.pathname === '/manifest.webmanifest';
  if (!isShell) return;

  // Network first (so updates land immediately on the LAN), cache as offline fallback.
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(isNav ? '/' : req, copy));
        }
        return res;
      })
      .catch(() => caches.match(isNav ? '/' : req).then((r) => r || caches.match('/')))
  );
});
