// STEP 4 — frontend silent-catch triage: auth_workspace.js (wave 4d)
//
// Triage of auth_workspace.js per the a/b/c rule found NO silent category (c)
// path and NO new category (b) whole-chain-failure site: login failures,
// workspace load failures, and save failures all rethrow or surface (they are
// not in the empty-catch set). The empty catches are all genuinely category (a)
// redundant defensive guards, now documented with rationale comments (no
// behavior change). The two fire-and-forget background calls (token keepalive,
// visibility-change pull) are intentional non-events per the whole-chain rule.
//
// These are source-assertion regression guards so a future edit can't silently
// un-reclassify the two most safety-relevant ones:
//   1. The fire-and-forget background pulls stay swallowed (token keepalive +
//      visibility-change sync) — not converted into throwing code.
//   2. The admin-token sessionStorage write stays swallowed + documented as
//      best-effort (token must never be placed in the URL).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SRC = fs.readFileSync(
  path.join(__dirname, '..', 'web', 'auth_workspace.js'),
  'utf8'
);

test('fire-and-forget background pulls stay swallowed (token keepalive)', () => {
  assert.match(
    SRC,
    /this\.ensureFreshToken\(\)\.catch\(\(\) => \{\}\);/,
    'token keepalive must remain fire-and-forget'
  );
  assert.match(
    SRC,
    /this\.pullWorkspaceIfNewer\(false\)\.catch\(\(\) => \{\}\)/,
    'visibility-change pull must remain fire-and-forget'
  );
  // Rationale comments present documenting the intentional swallow.
  assert.match(SRC, /keepalive is fire-and-forget/, 'token keepalive rationale comment');
  assert.match(SRC, /visibility-change pull is fire-and-forget/, 'pull rationale comment');
});

test('admin-token sessionStorage write stays swallowed + documented best-effort', () => {
  const idx = SRC.indexOf("sessionStorage.setItem('admin_workspace_token'");
  assert.ok(idx !== -1, 'admin token write should exist');
  const ctx = SRC.slice(Math.max(0, idx - 200), idx + 60);
  assert.match(ctx, /sessionStorage write is best-effort/, 'documented as best-effort');
  // Token is not placed in the URL (leak safety) — the comment must state this.
  assert.match(ctx, /token never placed in URL/, 'leak-safety rationale present');
});

test('auth failure paths are NOT part of the empty-catch set (they surface)', () => {
  // Login failure throws a surfaced error instead of swallowing.
  assert.match(SRC, /throw new Error\(msg\);/, 'login failure throws (surfaced)');
  assert.match(SRC, /throw new Error\('Failed to load workspace'\)/, 'workspace-load failure throws');
  // draft persistence/recovery documented as best-effort (category a).
  assert.match(SRC, /Draft persistence is best-effort/, 'draft persist rationale');
  assert.match(SRC, /Draft recovery is best-effort/, 'draft recover rationale');
});
