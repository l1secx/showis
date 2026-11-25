import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { buildContextMenu, contextNeighborCount, enabledMenuIndexes, nextMenuIndex, type ContextMenuItem } from "./context-menu.ts";

const edges = [
  { source: "a", target: "b" },
  { source: "a", target: "c" },
];

test("context-menu: komşu sayısı kendisi hariç", () => {
  assert.equal(contextNeighborCount("a", edges), 2);
  assert.equal(contextNeighborCount("b", edges), 1);
  assert.equal(contextNeighborCount("yok", edges), 0);
  assert.equal(contextNeighborCount(null, edges), 0);
});

test("context-menu: hedef düğümde eylemler etkin", () => {
  const items = buildContextMenu({
    targetId: "a",
    selection: new Set(["a"]),
    edges,
    visibleNodeCount: 5,
  });
  const byAction = new Map(items.map((item) => [item.action, item]));
  assert.equal(byAction.get("expand-neighbors")?.disabled, false);
  assert.equal(byAction.get("only-neighbors")?.disabled, false);
  assert.equal(byAction.get("hide")?.disabled, false);
  assert.equal(byAction.get("hide")?.danger, true);
  assert.equal(byAction.get("copy-label")?.disabled, false);
  assert.equal(byAction.get("path-from")?.disabled, false);
  assert.equal(byAction.get("focus-selection")?.disabled, false);
  assert.equal(byAction.get("clear-selection")?.disabled, false);
});

test("context-menu: komşusuz düğümde genişletme kapalı", () => {
  const items = buildContextMenu({
    targetId: "z",
    selection: new Set(["z"]),
    edges,
    visibleNodeCount: 3,
  });
  const byAction = new Map(items.map((item) => [item.action, item]));
  assert.equal(byAction.get("expand-neighbors")?.disabled, true);
  assert.equal(byAction.get("only-neighbors")?.disabled, true);
});

test("context-menu: son görünür düğüm gizlenemez", () => {
  const items = buildContextMenu({
    targetId: "a",
    selection: new Set(["a"]),
    edges,
    visibleNodeCount: 1,
  });
  const hide = items.find((item) => item.action === "hide");
  assert.equal(hide?.disabled, true);
});

test("context-menu: çoklu seçimde yol uçları kapalı", () => {
  const items = buildContextMenu({
    targetId: "a",
    selection: new Set(["a", "b"]),
    edges,
    visibleNodeCount: 6,
  });
  const byAction = new Map(items.map((item) => [item.action, item]));
  assert.equal(byAction.get("path-from")?.disabled, true);
  assert.equal(byAction.get("path-to")?.disabled, true);
});

test("context-menu: boş alanda yalnız seçim eylemleri", () => {
  const items = buildContextMenu({
    targetId: null,
    selection: new Set(["a", "b"]),
    edges,
    visibleNodeCount: 6,
  });
  const byAction = new Map(items.map((item) => [item.action, item]));
  assert.equal(byAction.get("expand-neighbors")?.disabled, true);
  assert.equal(byAction.get("hide")?.disabled, true);
  assert.equal(byAction.get("copy-label")?.disabled, true);
  assert.equal(byAction.get("path-from")?.disabled, true);
  assert.equal(byAction.get("focus-selection")?.disabled, false);
  assert.equal(byAction.get("clear-selection")?.disabled, false);
});

test("context-menu: klavye gezinmesi etkin öğeler arasında döner", () => {
  const items: ContextMenuItem[] = [
    { action: "expand-neighbors", label: "A", disabled: false },
    { action: "only-neighbors", label: "B", disabled: true },
    { action: "hide", label: "C", disabled: false },
    { action: "copy-label", label: "D", disabled: true },
    { action: "focus-selection", label: "E", disabled: false },
  ];
  assert.deepEqual(enabledMenuIndexes(items), [0, 2, 4]);
  assert.equal(nextMenuIndex(-1, 1, items), 0);
  assert.equal(nextMenuIndex(0, 1, items), 2);
  assert.equal(nextMenuIndex(4, 1, items), 0);
  assert.equal(nextMenuIndex(0, -1, items), 4);
  assert.equal(nextMenuIndex(2, -1, items), 0);
  assert.equal(nextMenuIndex(-1, -1, items), 4);
  const noneDisabled: ContextMenuItem[] = items.map((item) => ({ ...item, disabled: true }));
  assert.equal(nextMenuIndex(0, 1, noneDisabled), null);
});
