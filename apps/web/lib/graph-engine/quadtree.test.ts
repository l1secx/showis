import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { BarnesHutTree, bruteForce, computeForces, type Body } from "./quadtree.ts";

function seeded(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

function randomBodies(count: number, seed = 7): Body[] {
  const random = seeded(seed);
  const bodies: Body[] = [];
  for (let index = 0; index < count; index += 1) {
    bodies.push({
      x: random() * 800 - 400,
      y: random() * 800 - 400,
      mass: 1 + random() * 2,
    });
  }
  return bodies;
}

test("quadtree: theta=0 brute force ile aynı sonucu verir", () => {
  const bodies = randomBodies(90);
  const exact = bruteForce(bodies, { strength: 1, softening: 1 });
  const approximate = computeForces(bodies, { theta: 0, strength: 1, softening: 1 });
  assert.equal(approximate.length, exact.length);
  for (let index = 0; index < exact.length; index += 1) {
    const expected = exact[index];
    const actual = approximate[index];
    const scale = Math.max(1, Math.abs(expected.fx), Math.abs(expected.fy));
    assert.ok(
      Math.abs(actual.fx - expected.fx) <= scale * 1e-9,
      `indeks ${index} fx: beklenen ${expected.fx}, gelen ${actual.fx}`
    );
    assert.ok(
      Math.abs(actual.fy - expected.fy) <= scale * 1e-9,
      `indeks ${index} fy: beklenen ${expected.fy}, gelen ${actual.fy}`
    );
  }
});

test("quadtree: theta=0.9 yaklaşımı brute force ile yakın", () => {
  const bodies = randomBodies(400, 11);
  const exact = bruteForce(bodies, { strength: 1, softening: 1 });
  const approximate = computeForces(bodies, { theta: 0.9, strength: 1, softening: 1 });
  let exactTotal = 0;
  let approximateTotal = 0;
  for (let index = 0; index < bodies.length; index += 1) {
    exactTotal += Math.hypot(exact[index].fx, exact[index].fy);
    approximateTotal += Math.hypot(approximate[index].fx, approximate[index].fy);
  }
  const relativeError = Math.abs(approximateTotal - exactTotal) / exactTotal;
  assert.ok(relativeError < 0.1, `göreli hata çok yüksek: ${relativeError}`);
});

test("quadtree: çakışan noktalar NaN üretmez", () => {
  const bodies: Body[] = [
    { x: 5, y: 5, mass: 1 },
    { x: 5, y: 5, mass: 1 },
    { x: 5, y: 5, mass: 1 },
    { x: 5.000001, y: 5.000001, mass: 1 },
  ];
  const tree = new BarnesHutTree();
  tree.build(bodies);
  for (let index = 0; index < bodies.length; index += 1) {
    const force = tree.addForce(index, bodies, 0.9, 1, 1);
    assert.ok(Number.isFinite(force.fx) && Number.isFinite(force.fy));
  }
});

test("quadtree: boş girdi güvenli ve hücre sayısı sınırlı", () => {
  const empty = new BarnesHutTree();
  empty.build([]);
  assert.equal(empty.size, 0);
  assert.equal(empty.bounds, null);

  const bodies = randomBodies(400, 3);
  const tree = new BarnesHutTree();
  tree.build(bodies);
  assert.equal(tree.size, bodies.length);
  assert.ok(tree.cells > 0 && tree.cells <= bodies.length * 4, `hücre sayısı: ${tree.cells}`);
  const bounds = tree.bounds;
  assert.ok(bounds !== null);
  if (bounds) {
    for (const body of bodies) {
      assert.ok(body.x >= bounds.minX && body.x <= bounds.maxX);
      assert.ok(body.y >= bounds.minY && body.y <= bounds.maxY);
    }
  }
});
