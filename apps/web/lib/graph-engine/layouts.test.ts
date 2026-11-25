import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { circleLayout, computeLayout, gridLayout, hierarchicalLayout, radialLayout } from "./layouts.ts";
// @ts-ignore -- Aynı gerekçe.
import type { GEdge, GraphNodeInput } from "./types.ts";

function nodes(ids: string[]): GraphNodeInput[] {
  return ids.map((id) => ({ id, label: id, kind: "entity", weight: 1 }));
}

function edge(source: string, target: string): GEdge {
  return { id: `${source}-${target}`, source, target, type: "related", weight: 1 };
}

test("layouts: her düğüm için hedef üretir", () => {
  const input = nodes(["a", "b", "c", "d"]);
  const edges = [edge("a", "b"), edge("b", "c"), edge("c", "d")];
  for (const name of ["radial", "hierarchical", "circle", "grid"] as const) {
    const targets = computeLayout(name, input, edges);
    assert.equal(targets.size, input.length, name);
    for (const node of input) assert.ok(targets.has(node.id), `${name}: ${node.id}`);
  }
});

test("layouts: radial BFS halkaları merkezden uzaklaşır", () => {
  const input = nodes(["a", "b", "c", "d"]);
  const edges = [edge("a", "b"), edge("b", "c"), edge("c", "d")];
  const targets = radialLayout(input, edges, { center: "a", spacing: 100 });
  assert.deepEqual(targets.get("a"), { x: 0, y: 0 });
  const distance = (id: string) => Math.hypot(targets.get(id)!.x, targets.get(id)!.y);
  assert.ok(Math.abs(distance("b") - 100) < 1e-9);
  assert.ok(Math.abs(distance("c") - 200) < 1e-9);
  assert.ok(Math.abs(distance("d") - 300) < 1e-9);
});

test("layouts: radial merkez verilmezse en yüksek dereceli düğümü seçer", () => {
  const input = nodes(["a", "b", "c"]);
  const edges = [edge("a", "b"), edge("a", "c")];
  const targets = radialLayout(input, edges, { spacing: 50 });
  assert.deepEqual(targets.get("a"), { x: 0, y: 0 });
  assert.ok(Math.abs(Math.hypot(targets.get("b")!.x, targets.get("b")!.y) - 50) < 1e-9);
  assert.ok(Math.abs(Math.hypot(targets.get("c")!.x, targets.get("c")!.y) - 50) < 1e-9);
});

test("layouts: hierarchical derinliğe göre katmanlar", () => {
  const input = nodes(["a", "b", "c"]);
  const edges = [edge("a", "b"), edge("a", "c")];
  const targets = hierarchicalLayout(input, edges, { spacing: 100 });
  assert.equal(targets.get("a")!.y, 0);
  assert.equal(targets.get("b")!.y, 100);
  assert.equal(targets.get("c")!.y, 100);
  assert.notEqual(targets.get("b")!.x, targets.get("c")!.x);
});

test("layouts: circle eşit yarıçap ve eşit açı aralığı", () => {
  const input = nodes(["a", "b", "c", "d", "e", "f"]);
  const targets = circleLayout(input, [], { spacing: 80 });
  const radii = input.map((node) => Math.hypot(targets.get(node.id)!.x, targets.get(node.id)!.y));
  for (const radius of radii) assert.ok(Math.abs(radius - radii[0]) < 1e-9);
  const step = (Math.PI * 2) / input.length;
  const first = Math.atan2(targets.get("a")!.y, targets.get("a")!.x);
  const second = Math.atan2(targets.get("b")!.y, targets.get("b")!.x);
  assert.ok(Math.abs(Math.abs(second - first) - step) < 1e-9);
});

test("layouts: grid kareye yakın ve çakışmasız", () => {
  const input = nodes(["a", "b", "c", "d", "e"]);
  const targets = gridLayout(input, [], { spacing: 50 });
  const seen = new Set<string>();
  let maxRow = 0;
  for (const node of input) {
    const target = targets.get(node.id)!;
    seen.add(`${target.x}:${target.y}`);
    const row = Math.round(target.y / 50);
    maxRow = Math.max(maxRow, row);
  }
  assert.equal(seen.size, input.length);
  assert.equal(maxRow, 1);
  assert.equal(new Set(input.map((node) => targets.get(node.id)!.x)).size, 3);
});
