/**
 * SECURITY regression test for PCHost /manual path-traversal fix.
 *
 * Before the fix, /manual/* served sendFile() without a root, so `..` in the
 * path escaped the manual dir -> arbitrary file read (incl. the patient
 * SQLite DB). Now the resolved path is contained under manualDir (403).
 *
 * Raw-path http requests are used so `..` segments are NOT normalized away by
 * the URL parser before reaching the server.
 */
import assert from 'node:assert/strict';
import http from 'node:http';
import test from 'node:test';

import { app } from '../server.js';

let server;
let base;

test.before(async () => {
  server = http.createServer(app);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  base = server.address().port;
});

test.after(() => new Promise((resolve) => server.close(resolve)));

function rawRequest(rawPath) {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { host: '127.0.0.1', port: base, path: rawPath, method: 'GET' },
      (res) => {
        let body = '';
        res.on('data', (c) => (body += c));
        res.on('end', () => resolve({ status: res.statusCode, body }));
      },
    );
    req.on('error', reject);
    req.end();
  });
}

const TRAVERSALS = [
  '/manual/../../../../../etc/passwd',
  '/manual/../../../../etc/hostname',
  '/manual/../..',
  '/manual/../../README.md',
  '/manual/../../../../dreamcision/Clinical-Note-Generator/data/user_data.sqlite',
];

for (const p of TRAVERSALS) {
  test(`traversal blocked: ${p} -> 403`, async () => {
    const r = await rawRequest(p);
    assert.equal(r.status, 403, `expected 403 for ${p}, got ${r.status}: ${r.body}`);
  });
}

test('encoded %2f traversal does not escape (403 or 404, never 200)', async () => {
  // req.path keeps %2f encoded, so it is treated as a literal segment name ->
  // no escape. It must never serve file contents (200).
  const r = await rawRequest('/manual/..%2f..%2f..%2fetc%2fpasswd');
  assert.ok([403, 404].includes(r.status), `got ${r.status}: ${r.body}`);
});

test('valid manual index (dir/) -> 200', async () => {
  const r = await rawRequest('/manual/');
  assert.equal(r.status, 200);
});

test('valid manual index (exact /manual) -> 200 or 301 redirect', async () => {
  // express.static redirects /manual -> /manual/ (301); both are safe.
  const r = await rawRequest('/manual');
  assert.ok([200, 301].includes(r.status), `got ${r.status}`);
});

test('valid manual file -> 200', async () => {
  const r = await rawRequest('/manual/index.html');
  assert.equal(r.status, 200);
});
