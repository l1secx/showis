import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { MINIMAP_DEFAULT_SIZE, cameraForMinimapPoint, minimapOrigin, minimapTarget, minimapToWorld, minimapTransform, minimapViewRect, pointInMinimap, worldToMinimap } from "./minimap.ts";
// @ts-ignore -- Aynı gerekçe.
import type { BBox, Camera } from "./types.ts";

const viewport = { width: 800, height: 600 };
const size = MINIMAP_DEFAULT_SIZE;

test("minimap: boş kutu dönüşüm üretmez", () => {
  assert.equal(minimapTransform(null, size), null);
});

test("minimap: dünya kutusu dolgu içinde ortalanır", () => {
  const bbox: BBox = { minX: -400, minY: -200, maxX: 400, maxY: 200 };
  const transform = minimapTransform(bbox, size, 8);
  assert.ok(transform);
  const topLeft = worldToMinimap(transform, { x: bbox.minX, y: bbox.minY });
  const bottomRight = worldToMinimap(transform, { x: bbox.maxX, y: bbox.maxY });
  assert.ok(topLeft.x >= 8 - 1e-9 && topLeft.y >= 8 - 1e-9);
  assert.ok(bottomRight.x <= size.width - 8 + 1e-9);
  assert.ok(bottomRight.y <= size.height - 8 + 1e-9);
  // Kutu ortalanır.
  const center = worldToMinimap(transform, { x: 0, y: 0 });
  assert.ok(Math.abs(center.x - size.width / 2) < 1e-6);
  assert.ok(Math.abs(center.y - size.height / 2) < 1e-6);
});

test("minimap: dünya <-> mini harita gidiş-dönüşü", () => {
  const bbox: BBox = { minX: -120, minY: 30, maxX: 240, maxY: 190 };
  const transform = minimapTransform(bbox, size);
  assert.ok(transform);
  for (const point of [
    { x: -120, y: 30 },
    { x: 0, y: 0 },
    { x: 240, y: 190 },
  ]) {
    const mini = worldToMinimap(transform, point);
    const round = minimapToWorld(transform, mini);
    assert.ok(Math.abs(round.x - point.x) < 1e-6);
    assert.ok(Math.abs(round.y - point.y) < 1e-6);
  }
});

test("minimap: görünüm dikdörtgeni kamerayla ölçeklenir", () => {
  const bbox: BBox = { minX: -1000, minY: -1000, maxX: 1000, maxY: 1000 };
  const transform = minimapTransform(bbox, size);
  assert.ok(transform);
  const near: Camera = { x: 0, y: 0, zoom: 2 };
  const far: Camera = { x: 0, y: 0, zoom: 0.5 };
  const nearRect = minimapViewRect(near, viewport, transform);
  const farRect = minimapViewRect(far, viewport, transform);
  assert.ok(farRect.width > nearRect.width, "uzak kamera daha geniş pencere");
  assert.ok(farRect.height > nearRect.height);
  // Merkez aynıdır.
  assert.ok(
    Math.abs(
      nearRect.x + nearRect.width / 2 - (farRect.x + farRect.width / 2)
    ) < 1e-6
  );
});

test("minimap: tıklanan nokta kamerayı ortalar, zoom korunur", () => {
  const bbox: BBox = { minX: -200, minY: -200, maxX: 200, maxY: 200 };
  const transform = minimapTransform(bbox, size);
  assert.ok(transform);
  const camera: Camera = { x: 0, y: 0, zoom: 1.4 };
  const target = worldToMinimap(transform, { x: 100, y: -50 });
  const next = cameraForMinimapPoint(camera, transform, target);
  assert.ok(Math.abs(next.x - 100) < 1e-6);
  assert.ok(Math.abs(next.y + 50) < 1e-6);
  assert.equal(next.zoom, 1.4);
});

test("minimap: köşe yeri sağ alt, kenar boşluğu korunur", () => {
  const origin = minimapOrigin(viewport, size, 12);
  assert.equal(origin.x, viewport.width - size.width - 12);
  assert.equal(origin.y, viewport.height - size.height - 12);
  assert.ok(pointInMinimap({ x: origin.x + 2, y: origin.y + 2 }, origin, size));
  assert.ok(!pointInMinimap({ x: origin.x - 2, y: origin.y + 2 }, origin, size));
  // Küçük tuval taşmaz.
  const tiny = minimapOrigin({ width: 100, height: 80 }, size, 12);
  assert.equal(tiny.x, 0);
  assert.equal(tiny.y, 0);
});

test("minimap: minimapTarget yalnız mini harita kutusunda dünya döndürür", () => {
  const nodes = [
    { x: -100, y: -100 },
    { x: 100, y: 100 },
  ];
  const camera: Camera = { x: 0, y: 0, zoom: 1 };
  const origin = minimapOrigin(viewport, size, 12);
  const inside = minimapTarget(
    { x: origin.x + size.width / 2, y: origin.y + size.height / 2 },
    nodes,
    camera,
    viewport,
    size,
    12
  );
  assert.ok(inside);
  assert.ok(Math.abs(inside.x) < 1e-6);
  assert.ok(Math.abs(inside.y) < 1e-6);
  const outside = minimapTarget({ x: 10, y: 10 }, nodes, camera, viewport, size, 12);
  assert.equal(outside, null);
  const empty = minimapTarget(
    { x: origin.x + 5, y: origin.y + 5 },
    [],
    camera,
    viewport,
    size,
    12
  );
  assert.equal(empty, null);
});
