// Only a public offline page is cached. Orders, API responses and sign-in data never enter CacheStorage.
const cacheName = 'opsdemo-public-offline-v1';
self.addEventListener('install', event => event.waitUntil(caches.open(cacheName).then(cache => cache.add('/offline.html')).then(() => self.skipWaiting())));
self.addEventListener('activate', event => event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('opsdemo-public-offline-') && key !== cacheName).map(key => caches.delete(key)))).then(() => self.clients.claim())));
self.addEventListener('fetch', event => {
  if (event.request.mode === 'navigate' && new URL(event.request.url).origin === self.location.origin && !new URL(event.request.url).pathname.startsWith('/api') && !new URL(event.request.url).pathname.startsWith('/admin')) {
    event.respondWith(fetch(event.request).catch(() => caches.match('/offline.html')));
  }
});
