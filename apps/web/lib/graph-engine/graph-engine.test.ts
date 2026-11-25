import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { GraphEngine } from "./graph-engine.ts";

test("graph-engine: modül grafiği yüklenir (DOM'suz)", () => {
  assert.equal(typeof GraphEngine, "function");
});

test("graph-engine: görünürlük, yol, renk ve odak API'si vardır", () => {
  for (const method of ["setVisibility", "setPath", "setNodeColors", "setEdgeColors", "focus"] as const) {
    assert.equal(typeof GraphEngine.prototype[method], "function", method);
  }
});
