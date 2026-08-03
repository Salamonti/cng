/**
 * Contract checks for PCHost ↔ FastAPI (admin gate uses GET {FASTAPI_URL}/api/auth/me).
 * Run: npm test  (from PCHost/)
 */
import assert from 'node:assert/strict';
import http from 'node:http';
import test from 'node:test';

test('auth me URL joins like server.js', () => {
  const FASTAPI_URL = 'http://127.0.0.1:7860';
  const u = FASTAPI_URL.replace(/\/$/, '') + '/api/auth/me';
  assert.equal(u, 'http://127.0.0.1:7860/api/auth/me');
});

test('mock /api/auth/me returns JSON shape PCHost expects for admin gate', async () => {
  const server = http.createServer((req, res) => {
    if (req.url === '/api/auth/me' && req.method === 'GET') {
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(
        JSON.stringify({
          id: '00000000-0000-0000-0000-000000000001',
          email: 'admin@example.com',
          is_admin: true,
          is_approved: true,
          created_at: '2026-01-01T00:00:00',
        }),
      );
      return;
    }
    res.writeHead(404);
    res.end();
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  const url = `http://127.0.0.1:${port}/api/auth/me`;
  const r = await fetch(url, { headers: { Authorization: 'Bearer test-token' } });
  assert.equal(r.status, 200);
  const j = await r.json();
  assert.equal(j.is_admin, true);
  await new Promise((resolve) => server.close(resolve));
});
