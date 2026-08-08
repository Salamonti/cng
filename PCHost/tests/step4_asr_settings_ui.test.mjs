// STEP 4 — frontend silent-catch triage: asr_settings_ui.js
//
// refreshAsrCapabilities() previously swallowed fetch failures to both
// /asr/modes and /asr/capabilities with bare `catch (_) {}`. When BOTH
// endpoints were unreachable the ASR toggles silently disappeared with no
// trace. It now reports via window.reportClientError ONLY after the whole
// /asr/modes -> /asr/capabilities fallback chain has failed — a single
// endpoint failing while the other succeeds is a non-event and must not send
// noise to the incident store.
//
// The file is a browser IIFE, so we execute it inside a node `vm` sandbox that
// supplies a fake `window`/`global` (getApiBase, getAuthToken, fetch,
// reportClientError) and assert on the resulting reports.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SOURCE = fs.readFileSync(
  path.join(__dirname, '..', 'web', 'js', 'asr_settings_ui.js'),
  'utf8'
);

function makeSandbox() {
  const reports = [];
  const sandbox = {
    console,
    setTimeout,
    clearTimeout,
    // Defaults; tests override `fetch`.
    fetch: async () => ({ ok: true, json: async () => ({ ok: true }) }),
    reportClientError: (message, stack, kind) => reports.push({ message, stack, kind }),
    getApiBase: () => '/api',
    getAuthToken: () => 'tok',
    app: {},
    reports,
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  return sandbox;
}

// fetchSeq: array of per-call behaviours; 'ok' resolves 200, 'throw' rejects.
async function runRefresh(fetchSeq) {
  const sandbox = makeSandbox();
  sandbox.fetch = async (url) => {
    const behaviour = fetchSeq.shift();
    if (behaviour && behaviour === 'throw') {
      const err = new Error('network down: ' + url);
      err.stack = 'STACK:' + url;
      throw err;
    }
    if (behaviour && behaviour.data) {
      return { ok: true, json: async () => behaviour.data };
    }
    return { ok: true, json: async () => ({ ok: true }) };
  };
  vm.runInContext(SOURCE, sandbox); // defines sandbox.AsrSettingsUi
  const result = await sandbox.AsrSettingsUi.refreshAsrCapabilities();
  return { result, reports: sandbox.reports };
}

test('both ASR capability endpoints unreachable -> reports once per failing endpoint', async () => {
  const { result, reports } = await runRefresh(['throw', 'throw']);
  assert.equal(result, null, 'no capabilities -> null');
  assert.equal(reports.length, 2, 'reports both failures');
  assert.match(reports[0].message, /\/asr\/modes/);
  assert.match(reports[1].message, /\/asr\/capabilities/);
  assert.match(reports[0].stack, /STACK/);
});

test('first endpoint fails but fallback succeeds -> NO report (non-event)', async () => {
  const { result, reports } = await runRefresh(['throw', { data: { chunk_asr_enabled: true } }]);
  assert.ok(result, 'fallback capabilities returned');
  assert.equal(result.chunk_asr_enabled, true);
  assert.equal(reports.length, 0, 'single-endpoint failure is not noise');
});

test('both endpoints succeed -> returns capabilities, no report', async () => {
  const { result, reports } = await runRefresh([{ data: { modes: true } }, { data: { ok: true } }]);
  assert.ok(result);
  assert.equal(result.modes, true);
  assert.equal(reports.length, 0);
});

test('reportClientError absent -> refresh still returns null without throwing', async () => {
  const sandbox = makeSandbox();
  delete sandbox.reportClientError;
  sandbox.fetch = async () => { throw new Error('down'); };
  vm.runInContext(SOURCE, sandbox);
  const result = await sandbox.AsrSettingsUi.refreshAsrCapabilities();
  assert.equal(result, null);
});
