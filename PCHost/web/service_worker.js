// service_worker.js - PWA caching for DreamCision
const CACHE_NAME = 'dreamcision-pwa-20260819a';
const PRECACHE_URLS = [];
const NEVER_CACHE = [
  '/static/index.html',
  '/static/index111.html',
  '/index.html',
  '/index111.html',
  '/js/client_error_reporter.js',
  '/static/js/client_error_reporter.js',
  '/js/client_usage_reporter.js',
  '/static/js/client_usage_reporter.js',
  '/auth_workspace.js',
  '/static/auth_workspace.js',
  '/encounters_ui.js',
  '/static/encounters_ui.js',
  '/literature_ui.js',
  '/static/literature_ui.js',
  '/universal_audio_handler.js',
  '/static/universal_audio_handler.js',
  '/recording_recovery.js',
  '/static/recording_recovery.js',
  '/recording_durability.js',
  '/static/recording_durability.js',
  '/qa.html',
  '/static/qa.html',
  '/css/workspace.css',
  '/static/css/workspace.css',
  '/markdown_renderer.js',
  '/static/markdown_renderer.js',
  '/css/dreamcision-tokens.css',
  '/static/css/dreamcision-tokens.css',
  '/js/workspace_app.js',
  '/static/js/workspace_app.js',
  '/js/asr_settings.js',
  '/static/js/asr_settings.js',
  '/js/asr_settings_ui.js',
  '/static/js/asr_settings_ui.js',
  '/js/asr_chunk_pipeline.js',
  '/static/js/asr_chunk_pipeline.js',
  '/js/workspace_file_camera.js',
  '/static/js/workspace_file_camera.js',
  '/js/patient_materials_ui.js',
  '/static/js/patient_materials_ui.js',
  '/js/patient_materials.css',
  '/static/js/patient_materials.css',
  '/css/patient_materials.css',
  '/static/css/patient_materials.css',
  '/js/workspace_ui_state.js',
  '/static/js/workspace_ui_state.js',
  '/js/settings_connection.js',
  '/static/js/settings_connection.js',
  '/js/qa_side_panel.js',
  '/static/js/qa_side_panel.js',
  '/js/mobile_tools.js',
  '/static/js/mobile_tools.js',
  '/js/settings_drawer.js',
  '/static/js/settings_drawer.js',
  '/js/version_badge.js',
  '/static/js/version_badge.js',
  '/admin.html',
  '/static/admin.html',
  '/privacy.html',
  '/static/privacy.html',
  '/licenses.html',
  '/static/licenses.html',
  '/css/legal-pages.css',
  '/static/css/legal-pages.css',
  '/assets/dreamcision/logo-wordmark-h36.png',
  '/static/assets/dreamcision/logo-wordmark-h36.png',
  '/assets/dreamcision/logo-wordmark-h48.png',
  '/static/assets/dreamcision/logo-wordmark-h48.png',
  '/assets/dreamcision/logo-wordmark-h96.png',
  '/static/assets/dreamcision/logo-wordmark-h96.png',
  '/assets/dreamcision/favicon-48.png',
  '/static/assets/dreamcision/favicon-48.png',
  '/assets/dreamcision/icon-192.png',
  '/static/assets/dreamcision/icon-192.png',
  '/assets/dreamcision/icon-512.png',
  '/static/assets/dreamcision/icon-512.png',
  '/assets/dreamcision/dreamcision-header.svg',
  '/static/assets/dreamcision/dreamcision-header.svg',
  '/prompt-template-picker.html',
  '/static/prompt-template-picker.html',
  '/prompt_templates.jsonl',
  '/static/prompt_templates.jsonl',
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
    console.log(`[SW] Service Worker ${CACHE_NAME} installed - recording/ASR assets never cached`);
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.map((key) => (key !== CACHE_NAME ? caches.delete(key) : null)));
    await self.clients.claim();
    console.log(`[SW] Service Worker ${CACHE_NAME} activated - old caches cleared`);
  })());
});

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);
  const isHttp = url.protocol === 'http:' || url.protocol === 'https:';

  if (!isHttp) {
    return;
  }

  // Never intercept body-bearing requests. Safari can lose multipart upload bodies when
  // a service worker pass-through fetch handles an authenticated POST.
  if (req.method !== "GET" && req.method !== "HEAD") {
    return;
  }

  const hasAuth = req.headers.has('Authorization');
  if (hasAuth) {
    event.respondWith(fetch(req));
    return;
  }

  const isNeverCache = NEVER_CACHE.some(path =>
    url.pathname.endsWith(path) ||
    url.pathname.includes(path) ||
    url.pathname === path
  );
  if (isNeverCache) {
    event.respondWith(fetch(req));
    return;
  }

  const isApi = url.pathname.startsWith('/api');
  if (isApi) {
    event.respondWith(fetch(req));
    return;
  }

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

  if (req.method === 'GET') {
    event.respondWith((async () => {
      const cached = await caches.match(req, { ignoreVary: true });
      if (cached) {
        return cached;
      }
      try {
        const fresh = await fetch(req);
        if (fresh && fresh.ok && isHttp) {
          const cache = await caches.open(CACHE_NAME);
          cache.put(req, fresh.clone());
        }
        return fresh;
      } catch {
        return new Response('Offline', { status: 503, statusText: 'Offline' });
      }
    })());
    return;
  }

  event.respondWith(fetch(req));
});
