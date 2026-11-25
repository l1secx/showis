import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { applySelection, neighborIds, nextFocusIndex, selectionArray, sortForKeyboard, topConnectedNodes } from "./selection.ts";

const edges = [
  { source: "a", target: "b" },
  { source: "a", target: "c" },
  { source: "b", target: "d" },
  { source: "c", target: "c" },
];

test("selection: replace, add ve toggle davranışı", () => {
  const empty = new Set<string>();
  assert.deepEqual([...applySelection(empty, "a", "replace")], ["a"]);
  const two = applySelection(new Set(["a"]), "b", "add");
  assert.deepEqual([...two].sort(), ["a", "b"]);
  assert.deepEqual([...applySelection(two, "a", "toggle")], ["b"]);
  assert.deepEqual([...applySelection(two, "c", "toggle")].sort(), ["a", "b", "c"]);
  assert.equal(applySelection(two, "a", "replace").size, 1);
  assert.equal(applySelection(two, null, "add").size, 0);
});

test("selection: birincil kimlik dizinin sonuna taşınır", () => {
  const set = new Set(["a", "b", "c"]);
  assert.deepEqual(selectionArray(set, "b"), ["a", "c", "b"]);
  assert.deepEqual(selectionArray(set, null), ["a", "b", "c"]);
  assert.deepEqual(selectionArray(set, "yok"), ["a", "b", "c"]);
});

test("selection: komşular tekrarsız ve kendisiz", () => {
  assert.deepEqual(neighborIds("a", edges).sort(), ["b", "c"]);
  assert.deepEqual(neighborIds("d", edges), ["b"]);
  assert.deepEqual(neighborIds("c", edges).sort(), ["a"]);
  assert.deepEqual(neighborIds("yok", edges), []);
});

test("selection: en çok bağlantılı düğümler deterministik sıralanır", () => {
  const nodes = [
    { id: "a", label: "Alfa" },
    { id: "b", label: "Beta" },
    { id: "c", label: "Gama" },
    { id: "d", label: "Delta" },
  ];
  const top = topConnectedNodes(nodes, edges, 2);
  assert.deepEqual(
    top.map((item) => item.id),
    ["a", "b"]
  );
  assert.deepEqual(top.map((item) => item.degree), [2, 2]);
  assert.equal(topConnectedNodes(nodes, edges, 100).length, 4);
  assert.deepEqual(topConnectedNodes(nodes, edges, 0), []);
});

test("selection: klavye sırası etiket ve kimliğe göre", () => {
  const ordered = sortForKeyboard([
    { id: "2", label: "beta" },
    { id: "1", label: "Alfa" },
    { id: "3", label: "alfa" },
  ]);
  assert.deepEqual(
    ordered.map((item) => item.id),
    ["1", "3", "2"]
  );
});

test("selection: Tab indeksi sınırlarda canvas dışına bırakır", () => {
  assert.equal(nextFocusIndex(-1, 1, 3), 0);
  assert.equal(nextFocusIndex(-1, -1, 3), 2);
  assert.equal(nextFocusIndex(0, 1, 3), 1);
  assert.equal(nextFocusIndex(2, 1, 3), null);
  assert.equal(nextFocusIndex(0, -1, 3), null);
  assert.equal(nextFocusIndex(0, 1, 0), null);
  assert.equal(nextFocusIndex(0, 1, Number.NaN), null);
});
