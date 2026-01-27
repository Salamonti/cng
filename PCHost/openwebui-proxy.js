// Dedicated HTTPS reverse proxy for Open WebUI
const express = require('express');
const { createProxyMiddleware } = require('http-proxy-middleware');
const https = require('https');
const http = require('http');
const fs = require('fs');

const app = express();
app.set('trust proxy', true);

// Configuration
const HTTP_PORT = 8013;  // HTTP redirect port
const HTTPS_PORT = 8443; // HTTPS port for Open WebUI
const OPENWEBUI_URL = 'http://127.0.0.1:8035';

// SSL configuration (reuse existing certs)
const sslOptions = {
  key: fs.readFileSync('C:\\certs\\ieissa\\privkey.pem'),
  cert: fs.readFileSync('C:\\certs\\ieissa\\fullchain.pem')
};

// Redirect HTTP to HTTPS
app.use((req, res, next) => {
  if (!req.secure && req.get('x-forwarded-proto') !== 'https') {
    const host = req.get('host').replace(':' + HTTP_PORT, ':' + HTTPS_PORT);
    return res.redirect(301, 'https://' + host + req.url);
  }
  next();
});

// Proxy everything to Open WebUI (including WebSockets)
app.use(
  '/',
  createProxyMiddleware({
    target: OPENWEBUI_URL,
    changeOrigin: true,
    ws: true,  // Critical for WebSocket support
    xfwd: true,
    secure: false,
    proxyTimeout: 300000,
    timeout: 300000,
  })
);

// Start servers
http.createServer(app).listen(HTTP_PORT, '0.0.0.0', () => {
  console.log('HTTP server (redirect) listening on port', HTTP_PORT);
});

https.createServer(sslOptions, app).listen(HTTPS_PORT, '0.0.0.0', () => {
  console.log('HTTPS proxy server listening on port', HTTPS_PORT);
  console.log('Proxying to Open WebUI at', OPENWEBUI_URL);
  console.log('Access at: https://ieissa.com:' + HTTPS_PORT + '/');
});

// Graceful shutdown
process.on('SIGTERM', () => { console.log('Shutting down...'); process.exit(0); });
process.on('SIGINT', () => { console.log('Shutting down...'); process.exit(0); });
