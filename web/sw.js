const CACHE = 'krx-ai-v9-1-decision-dashboard';
const ASSETS = ['./index.html','./styles.css','./runtime-config.js','./app.js','./manifest.webmanifest','./assets/icon-192.png','./assets/icon-512.png'];
self.addEventListener('install', e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS))));
self.addEventListener('activate', e => e.waitUntil(Promise.all([caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))), self.clients.claim()])));
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (url.origin !== self.location.origin) return;
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
