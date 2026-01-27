// C:\PCHost\server.js
const express = require('express');
const { createProxyMiddleware } = require('http-proxy-middleware');
const cors = require('cors');
const https = require('https');
const http = require('http');
const fs = require('fs');
const path = require('path');

// Load configuration
const config = require('./config/server_config.json');

// App
const app = express();
app.set('trust proxy', true);

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
  allowedHeaders: ['Authorization', 'Content-Type', 'X-API-Key'],
  optionsSuccessStatus: 200,
}));

// No-cache for dynamic
app.use((req, res, next) => {
  res.set('Cache-Control', 'no-store, no-cache, must-revalidate, private');
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
// Llama gateway (uvicorn) on Windows host, default 7871. Override with env if needed.
const GATEWAY_URL = (process.env.LLAMA_GATEWAY_URL || 'http://127.0.0.1:7871');
// Open WebUI (Docker container)
const OPENWEBUI_URL = (process.env.OPENWEBUI_URL || 'http://127.0.0.1:8035');
console.log('FASTAPI_URL target =', FASTAPI_URL);
console.log('LLAMA_GATEWAY_URL target =', GATEWAY_URL);
console.log('OPENWEBUI_URL target =', OPENWEBUI_URL);

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
const proxyCommon = {
  target: FASTAPI_URL,
  changeOrigin: true,
  xfwd: true,
  secure: false,
  proxyTimeout: (config.backend_timeout || 300000),
  timeout: (config.backend_timeout || 300000),
  followRedirects: false,
};

// Proxy to Llama Gateway (unique routes only)
const llamaProxyCommon = {
  target: GATEWAY_URL,
  changeOrigin: true,
  xfwd: true,
  secure: false,
  proxyTimeout: (config.backend_timeout || 300000),
  timeout: (config.backend_timeout || 300000),
};

// Proxy to Open WebUI
const openwebuiProxyCommon = {
  target: OPENWEBUI_URL,
  changeOrigin: true,
  xfwd: true,
  secure: false,
  ws: true,  // Enable WebSocket support for real-time features
  proxyTimeout: (config.backend_timeout || 300000),
  timeout: (config.backend_timeout || 300000),
};

// New unique endpoints -> Llama Gateway on 7871
app.use(
  '/llama/generate',
  createProxyMiddleware({
    ...llamaProxyCommon,
    pathRewrite: () => '/api/generate',
  })
);

app.use(
  '/llama/check',
  createProxyMiddleware({
    ...llamaProxyCommon,
    pathRewrite: () => '/api/check',
  })
);

// WhisperX transcription endpoint -> FastAPI on 7860
app.use(
  '/whisperx',
  createProxyMiddleware({
    ...proxyCommon,
    pathRewrite: () => '/api/transcribe_diarized',
  })
);

// OCR endpoint -> FastAPI on 7860
app.use(
  '/ocr',
  createProxyMiddleware({
    ...proxyCommon,
    pathRewrite: () => '/api/ocr',
  })
);

// Note: Open WebUI proxy moved to dedicated server (openwebui-proxy.js)
// Running on port 8443 - access at https://ieissa.com:8443/

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
const webDir = path.join(__dirname, config.web_directory);
app.use(express.static(webDir, {
  index: ['index.html'],
  extensions: ['html', 'js', 'css', 'json'],
  setHeaders: (res, pth) => {
    if (pth.endsWith('service_worker.js')) res.setHeader('Content-Type', 'application/javascript');
    if (pth.endsWith('manifest.json')) res.setHeader('Content-Type', 'application/json');
  },
}));

// Named routes
app.get('/', (req, res) => res.sendFile(path.join(webDir, 'index.html')));
app.get('/qa', (req, res) => res.sendFile(path.join(webDir, 'qa.html')));

// SPA fallback for non-asset paths
app.get('*', (req, res) => {
  const staticExts = ['.html', '.js', '.css', '.json', '.png', '.jpg', '.ico', '.svg'];
  const hasExt = staticExts.some(function(ext){ return req.path.endsWith(ext); });
  if (hasExt) return res.status(404).send('File not found');
  return res.sendFile(path.join(webDir, 'index.html'));
});

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