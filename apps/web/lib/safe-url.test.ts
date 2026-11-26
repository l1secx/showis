import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { externalUrl, safeLinkHref } from "./safe-url.ts";

test("safe-url: yalnız http(s) harici bağlantı", () => {
  assert.equal(externalUrl("https://example.com/a"), "https://example.com/a");
  assert.equal(externalUrl("javascript:alert(1)"), null);
  assert.equal(externalUrl("data:text/html,x"), null);
  assert.equal(externalUrl("file:///etc/passwd"), null);
  assert.equal(externalUrl("/relative"), null);
});

test("safe-url: uygulama içi bağlantılar korunur", () => {
  assert.equal(safeLinkHref("/cases/1"), "/cases/1");
  assert.equal(safeLinkHref("#section"), "#section");
  assert.equal(safeLinkHref("mailto:a@b.c"), "mailto:a@b.c");
  assert.equal(safeLinkHref("javascript:alert(1)"), null);
  assert.equal(safeLinkHref("data:text/html,x"), null);
});
