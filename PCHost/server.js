// C:\PCHost\server.js
const express = require('express');
const { createProxyMiddleware } = require('http-proxy-middleware');
const cors = require('cors');
const https = require('https');
const http = require('http');
const fs = require('fs');
const path = require('path');
const compression = require('compression');


// Load configuration — pchost.* in repo-root service_endpoints.json overrides ports/backend_url/gateway
function _loadMergedConfig() {
  const base = require('./config/server_config.json');
  let ep = {};
  try {
    const epPath = process.env.SERVICE_ENDPOINTS_PATH || path.join(__dirname, '..', 'service_endpoints.json');
    if (fs.existsSync(epPath)) {
      ep = JSON.parse(fs.readFileSync(epPath, 'utf8')) || {};
    }
  } catch (e) {
    console.log('service_endpoints.json load skipped:', e.message);
  }
  const ph = ep.pchost && typeof ep.pchost === 'object' ? ep.pchost : {};
  const out = { ...base };
  for (const k of ['backend_url', 'http_port', 'https_port']) {
    if (ph[k] !== undefined && ph[k] !== null && ph[k] !== '') {
      out[k] = ph[k];
    }
  }
  return out;
}
const config = _loadMergedConfig();

// App
const app = express();
app.set('trust proxy', true);

// Compression (gzip) for all responses except streaming endpoints
app.use(compression({
  filter: (req, res) => {
    // Never compress streaming endpoints — compression buffers chunks and breaks SSE
    if (req.url && (req.url.includes('/generate_v8_stream') || req.url.includes('/transcribe_diarized') || req.url.includes('/chat_stream') || req.url.includes('/qa/vision'))) {
      return false;
    }
    return compression.filter(req, res);
  }
}));

const HTTP_PORT = process.env.HTTP_PORT || config.http_port;
const HTTPS_PORT = process.env.HTTPS_PORT || config.https_port;

// CORS (include auth headers)
app.use(cors({
  origin: [
    'http://localhost:' + HTTP_PORT,
    'https://localhost:' + HTTPS_PORT,
    'https://' + config.domain + ':' + HTTPS_PORT,
    'https://' + config.domain,
    'http://localhost:3000',
    'https://localhost:3443',
    'https://ieissa.com:3443',
    'https://ieissa.com',
    'https://notes.ieissa.com:3443',
    'https://notes.ieissa.com'
  ],
  credentials: true,
  allowedHeaders: ['Authorization', 'Content-Type', 'X-API-Key', 'X-ASR-Sticky-Index', 'X-ASR-Trace-Id'],
  exposedHeaders: ['X-ASR-Trace-Id'],
  optionsSuccessStatus: 200,
}));

// No-cache for dynamic API; static assets may be edge-cached (Net-P3)
app.use((req, res, next) => {
  const p = req.path || '';
  if (p.startsWith('/api') || p.startsWith('/admin')) {
    res.set('Cache-Control', 'no-store, no-cache, must-revalidate, private');
  } else if (p === '/' || /\.html$/i.test(p) || /auth_workspace\.js$/i.test(p) || /service_worker\.js$/i.test(p)) {
    res.set('Cache-Control', 'no-cache');
  } else if (/\.(js|css|png|jpg|jpeg|gif|webp|svg|ico|woff2?)$/i.test(p)) {
    res.set('Cache-Control', 'public, max-age=0');
  }
  next();
});

// SSL configuration (optional)
const keyPath = process.env.SSL_KEY_PATH || config.ssl_key_path;
const certPath = process.env.SSL_CERT_PATH || config.ssl_cert_path;
let sslOptions = null;
try {
  if (keyPath && certPath && fs.existsSync(keyPath) && fs.existsSync(certPath)) {
    sslOptions = { key: fs.readFileSync(keyPath), cert: fs.readFileSync(certPath) };
  }
} catch (e) {
  console.log('SSL cert load error:', e.message);
}

// Redirect HTTP->HTTPS if SSL available
if (sslOptions) {
  app.use((req, res, next) => {
    if (!req.secure && req.get('x-forwarded-proto') !== 'https') {
      var host = req.get('host');
      var redirected = 'https://' + host.replace(':' + HTTP_PORT, ':' + HTTPS_PORT) + req.url;
      return res.redirect(301, redirected);
    }
    next();
  });
}

// Backend targets
const FASTAPI_URL = (process.env.FASTAPI_URL || config.backend_url || 'http://127.0.0.1:7860');
console.log('FASTAPI_URL target =', FASTAPI_URL);

const webDir = path.join(__dirname, config.web_directory);
const adminHtmlPath = path.join(webDir, 'admin.html');

function _httpGetJson(targetUrl, headers = {}) {
  return new Promise((resolve, reject) => {
    let u;
    try {
      u = new URL(targetUrl);
    } catch (e) {
      reject(e);
      return;
    }
    const lib = u.protocol === 'https:' ? https : http;
    const opts = {
      hostname: u.hostname,
      port: u.port || (u.protocol === 'https:' ? 443 : 80),
      path: u.pathname + u.search,
      method: 'GET',
      headers,
      timeout: 12000,
    };
    const req = lib.request(opts, (res) => {
      let buf = '';
      res.on('data', (d) => { buf += d; });
      res.on('end', () => {
        let json = null;
        try {
          json = JSON.parse(buf || '{}');
        } catch (e) {
          json = null;
        }
        resolve({ status: res.statusCode || 0, json, raw: buf });
      });
    });
    req.on('error', reject);
    req.on('timeout', () => {
      try { req.destroy(); } catch (e) {}
      reject(new Error('timeout'));
    });
    req.setTimeout(12000);
    req.end();
  });
}

function _authMeUrl() {
  return FASTAPI_URL.replace(/\/$/, '') + '/api/auth/me';
}

/**
 * Require a Bearer token validated by FastAPI /api/auth/me (is_admin).
 *
 * The token is never accepted or placed in the URL (no ?access_token=): a
 * URL-embedded bearer token ends up in server access logs, proxy logs, and
 * browser history, any of which is enough to hijack the admin session. The
 * bootstrap script below fetches this same route with the token in an
 * Authorization header instead, then injects the returned admin page into
 * the current document -- the address bar and every log line downstream
 * only ever see the bare /admin.html path.
 */
function _sendAdminHtml403(res) {
  const body = `<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>Admin — access</title></head>
<body style="font-family:system-ui,sans-serif;padding:2rem;background:#130f1a;color:#e8e4f0;">
<p id="m">Checking session…</p>
<script>
(function(){
  var msg = document.getElementById('m');
  function denied() {
    msg.textContent = 'Administrator access required. Open the clinical app, sign in with an admin account, then use Admin from the menu (or bookmark after login).';
  }
  try {
    var q = new URLSearchParams(location.search || '');
    if (q.get('access_token')) {
      msg.textContent = 'Administrator access required. Tokens in the URL are no longer accepted (they leak into logs and browser history) -- sign in from the clinical workspace with an admin account instead.';
      return;
    }
    var t = sessionStorage.getItem('auth_access_token') || sessionStorage.getItem('admin_workspace_token');
    if (t) {
      fetch('/admin.html', { headers: { Authorization: 'Bearer ' + t } })
        .then(function (r) {
          if (!r.ok) { denied(); return; }
          return r.text().then(function (html) {
            document.open();
            document.write(html);
            document.close();
          });
        })
        .catch(denied);
      return;
    }
  } catch (e) {}
  denied();
})();
</script>
</body></html>`;
  res.status(403).set('Content-Type', 'text/html; charset=utf-8').send(body);
}

app.get('/admin.html', (req, res) => {
  let token = '';
  const auth = req.headers.authorization || '';
  if (typeof auth === 'string' && auth.toLowerCase().startsWith('bearer ')) {
    token = auth.slice(7).trim();
  }
  if (!token) {
    _sendAdminHtml403(res);
    return;
  }
  _httpGetJson(_authMeUrl(), { Authorization: 'Bearer ' + token })
    .then(({ status, json }) => {
      if (status === 200 && json && json.is_admin === true) {
        res.sendFile(adminHtmlPath);
        return;
      }
      _sendAdminHtml403(res);
    })
    .catch(() => {
      res.status(503).type('text/plain').send('Auth service unreachable. Check FASTAPI_URL and that the API is running.');
    });
});

// Direct connectivity check (bypasses proxy middleware)
app.get('/fastapi-check', (req, res) => {
  const url = FASTAPI_URL.replace(/\/$/, '') + '/api/health';
  http.get(url, (r) => {
    let buf = '';
    r.on('data', d => buf += d);
    r.on('end', () => {
      res.status(r.statusCode || 500).set('content-type', 'application/json').send(buf || '{}');
    });
  }).on('error', (e) => {
    res.status(502).json({ error: e.message, target: url });
  });
});

// Proxy to FastAPI (keep /api prefix, and also proxy legacy root routes)
const DEFAULT_BACKEND_TIMEOUT = config.backend_timeout || 300000;
const STREAM_BACKEND_TIMEOUT = config.stream_timeout || 900000;
// Note generation is now validated end-to-end (WO-1) before it returns, so a
// slow generation and a hung backend are otherwise indistinguishable to the
// browser. CNG's own request timeout is 60s; this sits just above it so the
// browser never waits on a backend that has already given up. Left separate
// from STREAM_BACKEND_TIMEOUT, which still covers /api/transcribe_diarized —
// diarizing a long recording is a different, legitimately slower workload.
const GENERATION_BACKEND_TIMEOUT = config.generation_timeout || 90000;

const proxyCommon = {
  target: FASTAPI_URL,
  changeOrigin: true,
  xfwd: true,
  secure: false,
  proxyTimeout: DEFAULT_BACKEND_TIMEOUT,
  timeout: DEFAULT_BACKEND_TIMEOUT,
  followRedirects: false,
};

const streamProxyCommon = {
  ...proxyCommon,
  proxyTimeout: STREAM_BACKEND_TIMEOUT,
  timeout: STREAM_BACKEND_TIMEOUT,
};

const generationProxyCommon = {
  ...proxyCommon,
  proxyTimeout: GENERATION_BACKEND_TIMEOUT,
  timeout: GENERATION_BACKEND_TIMEOUT,
};

// (Removed) Llama Gateway proxy — no longer needed on Linux/userver.
// (Removed) `/whisperx` alias to /api/transcribe_diarized — no longer used by any
// client. Use /api/transcribe_diarized directly.

// OCR endpoint -> FastAPI on 7860
app.use(
  '/ocr',
  createProxyMiddleware({
    ...proxyCommon,
    pathRewrite: () => '/api/ocr',
  })
);


// Long-running generation / ASR routes (G5)
// NOTE: app.use mounts strip the matched prefix, so the remainder for an exact
// hit is "/" — appending it produced a trailing slash (".../generate_v8_stream/")
// which made FastAPI 307-redirect to an https://...:7860 URL (xfwd proto) that the
// browser can't follow ("failed to fetch"). Emit the canonical path + any extra
// sub-path/query without introducing a lone trailing slash.
const streamPathRewrite = (basePath) => (path) => {
  const remainder = !path || path === '/' ? '' : path;
  return basePath + remainder;
};
app.use(
  '/api/generate_v8_stream',
  createProxyMiddleware({
    ...generationProxyCommon,
    pathRewrite: streamPathRewrite('/api/generate_v8_stream'),
  })
);
app.use(
  '/api/transcribe_diarized',
  createProxyMiddleware({
    ...streamProxyCommon,
    pathRewrite: streamPathRewrite('/api/transcribe_diarized'),
  })
);

// API namespace - preserve /api prefix and trailing slashes
app.use(
  '/api',
  createProxyMiddleware({
    ...proxyCommon,
    pathRewrite: (path, req) => {
      // CRITICAL: When middleware is mounted at /api, the path parameter
      // arrives WITHOUT the /api prefix already stripped by Express!
      // Example: browser sends /api/auth/login
      //          pathRewrite receives /auth/login (WITHOUT /api)
      // So we must add /api back!
      
      const hasTrailingSlash = path.endsWith('/');
      let rewritten = '/api' + path; // Add /api prefix back
      
      // Normalize duplicate slashes
      rewritten = rewritten.replace(/\/+/g, '/');
      
      // Restore trailing slash if it was there
      if (hasTrailingSlash && !rewritten.endsWith('/')) {
        rewritten += '/';
      }
      
      return rewritten;
    },
  })
);

// Admin namespace - same logic
app.use(
  '/admin',
  createProxyMiddleware({
    ...proxyCommon,
    pathRewrite: (path, req) => {
      // Same as above - Express strips /admin prefix before pathRewrite
      const hasTrailingSlash = path.endsWith('/');
      let rewritten = '/admin' + path; // Add /admin prefix back
      
      rewritten = rewritten.replace(/\/+/g, '/');
      
      if (hasTrailingSlash && !rewritten.endsWith('/')) {
        rewritten += '/';
      }
      
      return rewritten;
    },
  })
);

// Node health
app.get('/health', (req, res) => {
  res.json({ status: 'OK', timestamp: new Date().toISOString(), fastapi_target: FASTAPI_URL });
});

// Static web files
app.use(express.static(webDir, {
  index: ['index.html'],
  extensions: ['html', 'js', 'css', 'json'],
  setHeaders: (res, pth) => {
    if (pth.endsWith('service_worker.js')) res.setHeader('Content-Type', 'application/javascript');
    if (pth.endsWith('manifest.json')) res.setHeader('Content-Type', 'application/json');
    // Cache versioned static assets (1 year, immutable)
    if (/\.(js|css|png|jpg|jpeg|gif|svg|woff|woff2|ttf|ico|webp)$/.test(pth)) {
      if (/[?&]v=/.test(pth)) {
        res.setHeader('Cache-Control', 'public, max-age=31536000, immutable');
      } else {
        res.setHeader('Cache-Control', 'public, max-age=86400');
      }
    }
  },
}));

// Named routes
app.get('/', (req, res) => res.sendFile(path.join(webDir, 'index.html')));
app.get('/qa', (req, res) => res.sendFile(path.join(webDir, 'qa.html')));

// User manual (static site served at /manual)
const manualDir = path.resolve(__dirname, '..', 'docs', 'manual', 'site');
if (fs.existsSync(manualDir)) {
  app.use('/manual', express.static(manualDir, { index: 'index.html', dotfiles: 'deny' }));
  app.get('/manual', (req, res) => res.sendFile(path.join(manualDir, 'index.html')));
  app.get('/manual/*', (req, res) => {
    // SECURITY: resolve and enforce containment so `..` / absolute paths
    // cannot escape manualDir (was: sendFile without root, path traversal
    // -> arbitrary file read, incl. the patient SQLite DB).
    const rel = req.path.replace(/^\/manual\/?/, '');
    const resolved = path.resolve(manualDir, rel);
    if (resolved !== manualDir && !resolved.startsWith(manualDir + path.sep)) {
      return res.status(403).send('Forbidden');
    }
    if (req.path.endsWith('/')) {
      return res.sendFile(path.join(resolved, 'index.html'));
    }
    return res.sendFile(resolved);
  });
}

// SPA fallback for non-asset paths
app.get('*', (req, res) => {
  const staticExts = ['.html', '.js', '.css', '.json', '.png', '.jpg', '.ico', '.svg'];
  const hasExt = staticExts.some(function(ext){ return req.path.endsWith(ext); });
  if (hasExt) return res.status(404).send('File not found');
  return res.sendFile(path.join(webDir, 'index.html'));
});

if (require.main === module) {
  // Start servers
  const httpServer = http.createServer(app);
  httpServer.listen(HTTP_PORT, config.host, () => {
    console.log('HTTP server listening on', config.host + ':' + HTTP_PORT);
  });

  if (sslOptions) {
    const httpsServer = https.createServer(sslOptions, app);
    httpsServer.listen(HTTPS_PORT, config.host, () => {
      console.log('HTTPS server listening on', config.host + ':' + HTTPS_PORT);
    });
  } else {
    console.log('HTTPS not enabled (no SSL certs found)');
  }

  // Graceful shutdown
  process.on('SIGTERM', () => { console.log('Server shutting down (SIGTERM)'); process.exit(0); });
  process.on('SIGINT', () => { console.log('Server shutting down (SIGINT)'); process.exit(0); });
}

// Exported for tests (route/integration). server.js no longer auto-listens when imported.
module.exports = { app, sslOptions };
