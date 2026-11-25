import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { SpatialIndex, pointToSegmentDistance } from "./spatial-index.ts";
// @ts-ignore -- Aynı gerekçe.
import type { GEdge, GNode } from "./types.ts";

function node(id: string, x: number, y: number, weight = 1): GNode {
  return { id, label: id, kind: "entity", weight, x, y, vx: 0, vy: 0 };
}

function edge(id: string, source: string, target: string): GEdge {
  return { id, source, target, type: "related", weight: 1 };
}

test("spatial-index: yarıçap içindeki düğümü bulur, uzağı bulmaz", () => {
  const index = new SpatialIndex(32);
  const nodes = [node("a", 0, 0), node("b", 300, 300, 4)];
  index.rebuild(nodes);
  assert.equal(index.hitNode(0, 0)?.id, "a");
  assert.equal(index.hitNode(3, 3)?.id, "a");
  assert.equal(index.hitNode(200, 200), null);
  assert.equal(index.hitNode(300, 300)?.id, "b");
});

test("spatial-index: en yakın düğümü önce döndürür", () => {
  const index = new SpatialIndex(32);
  // Yarıçap (4 + 2.4*sqrt(20) ~= 14.7) her iki düğümün de (0,0) noktasını kapsamasını sağlar.
  index.rebuild([node("far", 10, 0, 20), node("near", 2, 0, 20)]);
  const hits = index.nodeHits(0, 0);
  assert.equal(hits[0]?.node.id, "near");
  assert.equal(hits.length, 2);
});

test("spatial-index: kenar çizgisine mesafe ile hit-test", () => {
  const index = new SpatialIndex(32);
  const nodes = [node("a", 0, 0), node("b", 100, 0)];
  const edges = [edge("ab", "a", "b")];
  index.rebuild(nodes, edges);
  assert.equal(index.hitEdge(50, 2, 5)?.id, "ab");
  assert.equal(index.hitEdge(50, 40, 5), null);
  const hit = index.edgeHit(50, 3, 5);
  assert.ok(hit);
  assert.ok(Math.abs(hit.distance - 3) < 1e-9);
});

test("spatial-index: dikdörtgen sorgusu dünya koordinatı kullanır", () => {
  const index = new SpatialIndex(32);
  index.rebuild([node("inside", 20, 20), node("outside", 500, 500)]);
  const found = index.nodesInRect({ x: 0, y: 0, width: 100, height: 100 });
  assert.deepEqual(found.map((item) => item.id), ["inside"]);
});

test("spatial-index: yeniden indeksleme eski hücreleri temizler", () => {
  const index = new SpatialIndex(32);
  index.rebuild([node("a", 0, 0)]);
  index.rebuild([node("b", 500, 500)]);
  assert.equal(index.hitNode(0, 0), null);
  assert.equal(index.hitNode(500, 500)?.id, "b");
});

test("spatial-index: nokta-doğru parçası mesafesi", () => {
  assert.equal(pointToSegmentDistance(50, 10, 0, 0, 100, 0), 10);
  assert.equal(pointToSegmentDistance(-20, 0, 0, 0, 100, 0), 20);
  assert.equal(pointToSegmentDistance(120, 0, 0, 0, 100, 0), 20);
});
