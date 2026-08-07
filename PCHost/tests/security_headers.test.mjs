// Step 5 (H-9 / M-6): security headers + HSTS gated on TLS edge + loopback bind.
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const { app } = await import(path.join(__dirname, '..', 'server.js'));

function request(opts, headers = {}) {
  return new Promise((resolve, reject) => {
    const server = http.createServer(app);
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      const r = http.request(
        { host: '127.0.0.1', port, path: opts.path || '/', method: opts.method || 'GET', headers },
        (res) => { let b = ''; res.on('data', (c) => (b += c)); res.on('end', () => { server.close(); resolve({ status: res.statusCode, headers: res.headers, body: b }); }); }
      );
      r.on('error', (e) => { server.close(); reject(e); });
      r.end();
    });
  });
}

test('loopback plaintext response has baseline security headers', async () => {
  const res = await request({ path: '/' });
  assert.equal(res.headers['x-content-type-options'], 'nosniff');
  assert.equal(res.headers['x-frame-options'], 'DENY');
  assert.equal(res.headers['referrer-policy'], 'no-referrer');
  // Plaintext request (no X-Forwarded-Proto) must NOT get HSTS.
  assert.equal(res.headers['strict-transport-security'], undefined);
});

test('HSTS sent when behind TLS edge (X-Forwarded-Proto: https)', async () => {
  const res = await request({ path: '/' }, { 'x-forwarded-proto': 'https' });
  assert.match(res.headers['strict-transport-security'] || '', /max-age=31536000/);
});
