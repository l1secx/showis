import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { SCAN_MODES, normalizeScanMode } from "./scan-modes.ts";

test("scan-modes: yalnız O1/O2/O3 kabul edilir", () => {
  assert.deepEqual(
    SCAN_MODES.map((mode) => mode.value),
    ["O1", "O2", "O3"]
  );
  assert.equal(normalizeScanMode("o3"), "O3");
  assert.equal(normalizeScanMode(" O2 "), "O2");
  assert.equal(normalizeScanMode("O4"), null);
  assert.equal(normalizeScanMode(undefined), null);
});
