// Step 9 (M-7): PCHost consumes RAG_URL / SEARXNG_URL from the env block of
// service_endpoints.json (the single source of truth), validates the endpoint
// config at startup, and reports the downstream endpoints on /health. The web
// server must not crash when a downstream URL is absent or malformed -- it
// must just surface it loudly.
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const { app, validateEndpoints, config } = await import(path.join(__dirname, '..', 'server.js'));

function request(pathname) {
  return new Promise((resolve, reject) => {
    const server = http.createServer(app);
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      const r = http.request({ host: '127.0.0.1', port, path: pathname }, (res) => {
        let b = ''; res.on('data', (c) => (b += c));
        res.on('end', () => { server.close(); resolve({ status: res.statusCode, body: JSON.parse(b) }); });
      });
      r.on('error', (e) => { server.close(); reject(e); });
      r.end();
    });
  });
}

test('M-7: /health reports RAG_URL and SEARXNG_URL consumed from service_endpoints.json', async () => {
  const res = await request('/health');
  assert.equal(res.status, 200);
  // The live service_endpoints.json carries both downstream endpoints.
  assert.equal(res.body.downstream.rag_url, 'http://127.0.0.1:8007');
  assert.equal(res.body.downstream.searxng_url, 'http://127.0.0.1:8083/search');
  assert.equal(res.body.endpoint_warning, null);
});

test('M-7: endpoints consumed into config at load', () => {
  assert.equal(config.rag_url, 'http://127.0.0.1:8007');
  assert.equal(config.searxng_url, 'http://127.0.0.1:8083/search');
});

test('M-7: validateEndpoints flags malformed and missing URLs (pure)', () => {
  // malformed RAG_URL
  const w1 = validateEndpoints('not-a-url', 'http://127.0.0.1:8083/search', 'http://127.0.0.1:7860');
  assert.deepEqual(w1.malformed, ['RAG_URL=not-a-url']);
  assert.deepEqual(w1.missing, []);
  // missing SEARXNG + backend
  const w2 = validateEndpoints('http://127.0.0.1:8007', '', '');
  assert.deepEqual(w2.missing, ['SEARXNG_URL', 'backend_url']);
  assert.deepEqual(w2.malformed, []);
  // all good -> no warning
  const w3 = validateEndpoints('http://127.0.0.1:8007', 'http://127.0.0.1:8083/search', 'http://127.0.0.1:7860');
  assert.deepEqual(w3, { missing: [], malformed: [] });
});
