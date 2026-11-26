import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { escapeCsvCell } from "./csv.ts";

test("csv: formül başlangıçlarını tek tırnakla etkisizleştirir", () => {
  for (const raw of ["=1+1", "+SUM(A1)", "-2+3", "@cmd", "\t=1", "  =1"]) {
    assert.equal(escapeCsvCell(raw), `"'${raw}"`);
  }
});

test("csv: normal değeri ve çift tırnağı korur", () => {
  assert.equal(escapeCsvCell("github"), '"github"');
  assert.equal(escapeCsvCell('a"b'), '"a""b"');
  assert.equal(escapeCsvCell(null), '""');
});
