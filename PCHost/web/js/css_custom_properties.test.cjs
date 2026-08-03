'use strict';

// Regression test (P3-5): --dc-border and --dc-violet-glow were referenced
// via var(--dc-border) / var(--dc-violet-glow) in 15 places across
// workspace.css, admin.html, qa.html and legal-pages.css but never defined
// in dreamcision-tokens.css. With no fallback argument, an undefined custom
// property makes the whole declaration invalid, so the browser silently
// drops it -- borders and button glow effects vanished app-wide with no
// console warning. This test scans every CSS/HTML file under web/ for
// var(--dc-*) usages (without a fallback) and asserts each one is defined
// in the single source-of-truth token file.

const test = require('node:test');
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const webDir = path.join(__dirname, '..');
const tokensFile = path.join(webDir, 'css', 'dreamcision-tokens.css');

function findFiles(dir, exts) {
    const out = [];
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        if (entry.name.startsWith('.')) continue;
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            out.push(...findFiles(full, exts));
        } else if (exts.includes(path.extname(entry.name))) {
            out.push(full);
        }
    }
    return out;
}

function definedTokens() {
    const src = fs.readFileSync(tokensFile, 'utf8');
    const defs = new Set();
    for (const m of src.matchAll(/(--dc-[a-zA-Z0-9-]+)\s*:/g)) {
        defs.add(m[1]);
    }
    return defs;
}

// var(--dc-foo) with no comma is a bare reference (undefined = whole
// declaration dropped). var(--dc-foo, fallback) is safe even if undefined.
function bareUsages(files) {
    const usages = [];
    for (const file of files) {
        const src = fs.readFileSync(file, 'utf8');
        for (const m of src.matchAll(/var\(\s*(--dc-[a-zA-Z0-9-]+)\s*([,)])/g)) {
            if (m[2] === ')') {
                usages.push({ name: m[1], file: path.relative(webDir, file) });
            }
        }
    }
    return usages;
}

test('every bare var(--dc-*) reference has a matching token definition', () => {
    const defs = definedTokens();
    assert.ok(defs.size > 10, 'sanity check: token file should define many tokens');

    const files = findFiles(webDir, ['.css', '.html']);
    const usages = bareUsages(files);
    assert.ok(usages.length > 10, 'sanity check: should find many var(--dc-*) usages');

    const undefined_ = usages.filter((u) => !defs.has(u.name));
    assert.deepStrictEqual(
        undefined_,
        [],
        `undefined custom properties referenced: ${JSON.stringify(undefined_)}`
    );
});
