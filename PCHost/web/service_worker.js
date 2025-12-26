// C:\PCHost\web\service_worker.js
// service_worker.js - PWA caching for Clinical Note Generator
const CACHE_NAME = 'clinical-notes-ocr-v14'; // Increment version to force update
const PRECACHE_URLS = [];
const NEVER_CACHE = [
  '/static/index.html',
  '/static/index111.html',
  '/index.html',
  '/index111.html',
  '/auth_workspace.js',        // CRITICAL: Never cache auth file
  '/static/auth_workspace.js', // CRITICAL: Never cache auth file
];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    for (const url of PRECACHE_URLS) {
      try {
        const response = await fetch(url, { method: 'HEAD' });
        if (response.ok) {
          await cache.add(url);
          console.log('[SW] Cached:', url);
        } else {
          console.log('[SW] Skipping (not found):', url);
        }
      } catch (err) {
        console.log('[SW] Skipping (error):', url);
      }
    }
    await self.skipWaiting();
    console.log('[SW] Service Worker v14 installed - auth_workspace.js never cached');
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.map((key) => (key !== CACHE_NAME ? caches.delete(key) : null)));
    await self.clients.claim();
    console.log('[SW] Service Worker v14 activated - old caches cleared');
  })());
});

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

// Fetch handler
self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);
  const isHttp = url.protocol === 'http:' || url.protocol === 'https:';
  
  if (!isHttp) {
    return;
  }
  
  // Check for Authorization header FIRST - pass through without modification
  const hasAuth = req.headers.has('Authorization');
  if (hasAuth) {
    console.log('[SW] Auth header detected, bypassing for:', url.pathname);
    // CRITICAL FIX: Just pass the request through as-is, don't modify it
    event.respondWith(fetch(req));
    return;
  }
  
  // NEVER cache these files - always fetch fresh
  const isNeverCache = NEVER_CACHE.some(path => 
    url.pathname.endsWith(path) || 
    url.pathname.includes(path) ||
    url.pathname === path
  );
  if (isNeverCache) {
    console.log('[SW] NEVER_CACHE - bypassing for:', url.pathname);
    event.respondWith(fetch(req));
    return;
  }
  
  // Bypass caching for ALL API requests (safer approach)
  const isApi = url.pathname.startsWith('/api');
  if (isApi) {
    console.log('[SW] API request, bypassing cache for:', url.pathname);
    event.respondWith(fetch(req));
    return;
  }
  
  // Network-first for HTML navigation
  const accept = req.headers.get('accept') || '';
  const isHTML = accept.includes('text/html') || req.mode === 'navigate';
  if (isHTML) {
    event.respondWith((async () => {
      try {
        const fresh = await fetch(req);
        const cache = await caches.open(CACHE_NAME);
        if (req.method === 'GET' && fresh && fresh.ok && isHttp) {
          cache.put(req, fresh.clone());
        }
        return fresh;
      } catch (e) {
        const cached = await caches.match(req, { ignoreSearch: true });
        return cached || caches.match('/static/index.html');
      }
    })());
    return;
  }
  
  // Cache-first for static GET assets (CSS, JS, images)
  // BUT: auth_workspace.js is excluded above in NEVER_CACHE
  if (req.method === 'GET') {
    event.respondWith((async () => {
      const cached = await caches.match(req);
      if (cached) {
        console.log('[SW] Serving from cache:', url.pathname);
        return cached;
      }
      try {
        const fresh = await fetch(req);
        if (fresh && fresh.ok && isHttp) {
          const cache = await caches.open(CACHE_NAME);
          cache.put(req, fresh.clone());
          console.log('[SW] Cached fresh copy:', url.pathname);
        }
        return fresh;
      } catch {
        return new Response('Offline', { status: 503, statusText: 'Offline' });
      }
    })());
    return;
  }
  
  // Fallback: just fetch
  event.respondWith(fetch(req));
});