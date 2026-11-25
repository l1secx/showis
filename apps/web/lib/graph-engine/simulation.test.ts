import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { Simulation, seedPositions } from "./simulation.ts";
// @ts-ignore -- Aynı gerekçe.
import type { GEdge, GNode } from "./types.ts";

function makeNodes(count: number, clusters: Array<string | null> = []): GNode[] {
  const nodes: GNode[] = [];
  for (let index = 0; index < count; index += 1) {
    nodes.push({
      id: `n${index}`,
      label: `Düğüm ${index}`,
      kind: index % 3 === 0 ? "platform" : "entity",
      weight: 1 + (index % 4),
      cluster: clusters[index] ?? null,
      x: Number.NaN,
      y: Number.NaN,
      vx: 0,
      vy: 0,
    });
  }
  return nodes;
}

function ringEdges(nodeCount: number, edgeCount: number): GEdge[] {
  const edges: GEdge[] = [];
  for (let index = 0; index < edgeCount; index += 1) {
    const source = index % nodeCount;
    const target = (index * 7 + 3) % nodeCount;
    edges.push({
      id: `e${index}`,
      source: `n${source}`,
      target: `n${target}`,
      type: "related",
      weight: 1,
      directed: index % 2 === 0,
    });
  }
  return edges;
}

function distance(a: GNode, b: GNode): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

test("simulation: aynı tohum ve girdi aynı yerleşimi üretir", () => {
  const nodesA = makeNodes(60);
  const nodesB = makeNodes(60);
  const edgesA = ringEdges(60, 110);
  const edgesB = ringEdges(60, 110);
  const first = new Simulation(nodesA, edgesA, { seed: 42, alphaDecay: 0.05 });
  const second = new Simulation(nodesB, edgesB, { seed: 42, alphaDecay: 0.05 });
  first.tick(30);
  second.tick(30);
  for (let index = 0; index < nodesA.length; index += 1) {
    assert.equal(nodesA[index].x, nodesB[index].x, `x farkı: ${index}`);
    assert.equal(nodesA[index].y, nodesB[index].y, `y farkı: ${index}`);
  }
});

test("simulation: farklı tohum farklı başlangıç üretir", () => {
  const nodesA = makeNodes(20);
  const nodesB = makeNodes(20);
  seedPositions(nodesA, 1);
  seedPositions(nodesB, 2);
  let differences = 0;
  for (let index = 0; index < nodesA.length; index += 1) {
    if (nodesA[index].x !== nodesB[index].x) differences += 1;
  }
  assert.ok(differences > 0);
});

test("simulation: alpha soğur ve yakınsar", () => {
  const nodes = makeNodes(80);
  const sim = new Simulation(nodes, ringEdges(80, 140), { seed: 3, alphaDecay: 0.08 });
  sim.tick(250);
  assert.ok(sim.isSettled(), `alpha=${sim.alpha}`);
  assert.ok(sim.alpha < 0.01);
  for (const node of nodes) {
    assert.ok(Number.isFinite(node.x) && Number.isFinite(node.y));
  }
  const snapshot = nodes.map((node) => ({ x: node.x, y: node.y }));
  sim.tick(5);
  for (let index = 0; index < nodes.length; index += 1) {
    assert.ok(Math.abs(nodes[index].x - snapshot[index].x) < 1e-6);
    assert.ok(Math.abs(nodes[index].y - snapshot[index].y) < 1e-6);
  }
});

test("simulation: fixed düğüm hareket etmez", () => {
  const nodes = makeNodes(5);
  nodes[0].x = 10;
  nodes[0].y = -10;
  nodes[0].fixed = true;
  const sim = new Simulation(nodes, [], { seed: 7 });
  sim.tick(40);
  assert.equal(nodes[0].x, 10);
  assert.equal(nodes[0].y, -10);
});

test("simulation: küme çekimi aynı küme üyelerini yakınlaştırır", () => {
  const clusters = ["a", "a", "a", "a", "b", "b", "b", "b"];
  const nodes = makeNodes(8, clusters);
  const sim = new Simulation(nodes, [], {
    seed: 5,
    clusterStrength: 0.5,
    clusterRadius: 280,
    chargeStrength: 400,
    alphaDecay: 0.04,
  });
  sim.tick(200);

  const intra: number[] = [];
  const inter: number[] = [];
  for (let first = 0; first < nodes.length; first += 1) {
    for (let second = first + 1; second < nodes.length; second += 1) {
      const target = nodes[first].cluster === nodes[second].cluster ? intra : inter;
      target.push(distance(nodes[first], nodes[second]));
    }
  }
  const average = (values: number[]) => values.reduce((sum, value) => sum + value, 0) / values.length;
  assert.ok(average(intra) < average(inter) * 0.5, `iç ${average(intra)}, dış ${average(inter)}`);
});

test("simulation: 2000 düğüm / 6000 kenar kaba süre testi", (context) => {
  const nodes = makeNodes(2000);
  const edges = ringEdges(2000, 6000);
  const sim = new Simulation(nodes, edges, { seed: 9 });
  const started = performance.now();
  sim.tick(10);
  const elapsed = performance.now() - started;
  context.diagnostic(`2000 düğüm / 6000 kenar, 10 tick: ${elapsed.toFixed(1)} ms`);
  assert.ok(elapsed < 5000, `10 tick çok yavaş: ${elapsed.toFixed(1)} ms`);
  assert.ok(sim.alpha > 0 && sim.alpha < 1);
  for (const node of nodes) {
    assert.ok(Number.isFinite(node.x) && Number.isFinite(node.y));
  }
});
