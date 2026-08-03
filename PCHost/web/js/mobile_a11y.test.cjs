'use strict';

// Regression test (P3-5): the mobile bottom-nav items, Tools bottom-sheet
// items, and patient-material category cards are all plain <div>s activated
// via onclick with no role/tabindex/keyboard handling -- unreachable by Tab
// and invisible to screen readers, since a div carries no default
// interactive semantics. Fixed by adding role="button" tabindex="0" to each
// element plus a delegated keydown listener (mobile_tools.js) that maps
// Enter/Space to a click, matching the native <button> activation contract
// these divs don't get for free. This test guards both halves: the markup
// attributes on every such div in index.html, and the keydown delegate
// actually existing in mobile_tools.js.

const test = require('node:test');
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const webDir = path.join(__dirname, '..');
const indexHtml = fs.readFileSync(path.join(webDir, 'index.html'), 'utf8');
const mobileToolsJs = fs.readFileSync(path.join(webDir, 'js', 'mobile_tools.js'), 'utf8');

function openTags(html, className) {
    const re = new RegExp(`<div class="${className}"[^>]*>`, 'g');
    return html.match(re) || [];
}

test('nav-item, tools-item, and pm-category-card divs are keyboard-focusable buttons', () => {
    const groups = {
        'nav-item': openTags(indexHtml, 'nav-item( active)?'),
        'tools-item': openTags(indexHtml, 'tools-item'),
        'pm-category-card': openTags(indexHtml, 'pm-category-card'),
    };

    for (const [name, tags] of Object.entries(groups)) {
        assert.ok(tags.length > 0, `expected to find ${name} elements in index.html`);
        for (const tag of tags) {
            assert.match(tag, /role="button"/, `${name} missing role="button": ${tag}`);
            assert.match(tag, /tabindex="0"/, `${name} missing tabindex="0": ${tag}`);
        }
    }
});

test('mobile_tools.js delegates Enter/Space activation to role=button elements', () => {
    assert.match(mobileToolsJs, /addEventListener\(['"]keydown['"]/);
    assert.match(mobileToolsJs, /\[role="button"\]/);
    assert.match(mobileToolsJs, /\.click\(\)/);
});
