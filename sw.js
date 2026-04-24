// Virginia Beach Viewer — Service Worker v1
// Network-first for data.json / recent.json / latest.json.
// The old v3 was cache-first (stale-while-revalidate) which made the viewer
// appear frozen on refresh, especially on mobile. Fall back to cache only on network failure.
const CACHE = 'vb-v1';
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(
  caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim())
));
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  const path = url.pathname;
  if (!path.endsWith('data.json') && !path.endsWith('recent.json') && !path.endsWith('latest.json')) return;
  // Stable cache key ignores the ?v= cache-busting param
  const cacheKey = new Request(url.origin + url.pathname);
  e.respondWith(
    fetch(e.request, { cache: 'no-store' }).then(resp => {
      if (resp && resp.ok) {
        const clone = resp.clone();
        caches.open(CACHE).then(cache => cache.put(cacheKey, clone));
      }
      return resp;
    }).catch(() => caches.open(CACHE).then(cache => cache.match(cacheKey).then(cached => cached || Response.error())))
  );
});
