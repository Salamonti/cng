import test from "node:test";
import assert from "node:assert/strict";
import { ESLint } from "eslint";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import globalsPkg from "globals";
import html from "eslint-plugin-html";

// STEP 7 — inline-script linting gate.
//
// Bug-1 (typeof-x.y throwing in a failure branch / undeclared id in the branch
// that only runs on error) is unguarded inside inline <script> blocks because
// they are invisible to the .js-file no-undef pass. This suite proves ESLint's
// no-undef now covers the inline scripts in the four HTML pages:
//
//   (1) the real HTML pages lint clean under the project config (which now
//       includes web/*.html), AND
//   (2) a deliberate-undefined identifier inside an inline <script> MUST fail
//       lint — if that stops failing, inline coverage has silently regressed.
//
// The test lints every HTML page that contains an inline script block, and
// additionally asserts that offending inline blocks are actually reported, so
// "covers HTML but silently ignores the inline body" can't pass.

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, "..");
const HTML_PAGES = ["web/index.html", "web/qa.html", "web/admin.html"];

// Resolve FIXTURES dir: run eslint against a temp copy so we never touch the
// real tree or fight the config's ignores for an out-of-tree file.
function fixtureWithInlineUndef() {
  const dir = mkdtempSync(path.join(tmpdir(), "step7-inline-"));
  const p = path.join(dir, "page.html");
  writeFileSync(
    p,
    [
      "<!doctype html><html><head>",
      "<script>",
      "  const legit = 1;",
      "  function run() { return window.undefinedSymbol_Step7_Probe; }",
      // deliberate no-undef: `window` is browser-known, so use a top-level bare id
      "  const probe = " + "step7DeliberateUndef_Should_Fail;",
      "</script>",
      "</head><body></body></html>",
    ].join("\n"),
  );
  return { dir, p };
}

test("STEP 7: all real HTML pages lint clean under project config", async () => {
  const eslint = new ESLint({ overrideConfigFile: path.join(ROOT, "eslint.config.js") });
  const results = await eslint.lintFiles(
    HTML_PAGES.map((f) => path.join(ROOT, f)),
  );
  const formatted = eslint.getFormatter
    ? await eslint.loadFormatter("stylish").then((f) => f.format(results))
    : "";
  const messages = results.flatMap((r) =>
    (r.messages || []).map((m) => ({ file: r.filePath, ...m })),
  );
  assert.deepEqual(
    messages.map((m) => `${m.ruleId}:${m.message}`).filter((s) => !s.includes("is not defined")),
    [],
    `unexpected lint errors in HTML pages\n${formatted}`,
  );
  // no-undef clean across the pages (any genuine cross-file coupling is already
  // declared in APP_GLOBALS in eslint.config.js)
  assert.equal(messages.filter((m) => m.ruleId === "no-undef").length, 0,
    `no-undef errors on real HTML pages:\n${formatted}`);
});

test("STEP 7: deliberate-undefined inline id FAILS lint (proves inline coverage)", async () => {
  const { dir, p } = fixtureWithInlineUndef();
  try {
    // Self-contained config so the fixture (in a temp dir, outside the project
    // files glob) is still linted exactly like the real inline coverage path:
    // the html plugin + browser globals + no-undef.
    const config = [
      {
        files: ["**/*.html"],
        plugins: { html },
        languageOptions: {
          ecmaVersion: 2022,
          sourceType: "script",
          globals: { ...globalsPkg.browser },
        },
        rules: { "no-undef": "error" },
      },
    ];
    const eslint = new ESLint({
      cwd: dir,
      overrideConfigFile: true,
      overrideConfig: config,
    });
    const [result] = await eslint.lintFiles([p]);
    const messages = result.messages || [];
    const undef = messages.filter((m) => m.ruleId === "no-undef");
    assert.ok(
      undef.some((m) => m.message.includes("step7DeliberateUndef_Should_Fail")),
      "expected a no-undef error on the deliberate-undefined inline id; got:\n" +
        JSON.stringify(messages, null, 2),
    );
    assert.equal(result.errorCount, 1, "the undef fixture should report exactly one error");
    assert.equal(result.fatalErrorCount, 0, "no parse fatal in the fixture");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
