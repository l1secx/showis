import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { fitToView, panBy, screenRectToWorld, screenToWorld, worldToScreen, zoomAt } from "./camera.ts";
// @ts-ignore -- Aynı gerekçe.
import type { Camera } from "./types.ts";

const viewport = { width: 800, height: 600 };

test("camera: dünya -> ekran -> dünya gidiş-dönüşü", () => {
  const camera: Camera = { x: 120, y: -40, zoom: 1.7 };
  const points = [
    { x: 0, y: 0 },
    { x: 250, y: -130 },
    { x: -800, y: 640 },
  ];
  for (const point of points) {
    const screen = worldToScreen(camera, viewport, point);
    const roundTrip = screenToWorld(camera, viewport, screen);
    assert.ok(Math.abs(roundTrip.x - point.x) < 1e-9);
    assert.ok(Math.abs(roundTrip.y - point.y) < 1e-9);
  }
});

test("camera: zoom imlecin altındaki dünya noktasını sabit tutar", () => {
  const camera: Camera = { x: 0, y: 0, zoom: 1 };
  const cursor = { x: 650, y: 120 };
  const before = screenToWorld(camera, viewport, cursor);
  const zoomed = zoomAt(camera, viewport, cursor.x, cursor.y, 2.5);
  const after = screenToWorld(zoomed, viewport, cursor);
  assert.ok(Math.abs(after.x - before.x) < 1e-9);
  assert.ok(Math.abs(after.y - before.y) < 1e-9);
  assert.equal(zoomed.zoom, 2.5);
});

test("camera: zoom sınırları uygulanır", () => {
  const camera: Camera = { x: 0, y: 0, zoom: 1 };
  const tooFar = zoomAt(camera, viewport, 400, 300, 1000, 0.1, 4);
  assert.equal(tooFar.zoom, 4);
  const tooClose = zoomAt(camera, viewport, 400, 300, 0.0001, 0.1, 4);
  assert.equal(tooClose.zoom, 0.1);
});

test("camera: pan ekran pikselini dünya birimine çevirir", () => {
  const camera: Camera = { x: 0, y: 0, zoom: 2 };
  const moved = panBy(camera, 40, -20);
  assert.equal(moved.x, -20);
  assert.equal(moved.y, 10);
  assert.equal(moved.zoom, 2);
});

test("camera: fitToView kutuyu dolgu içinde ortalar", () => {
  const bbox = { minX: -100, minY: -50, maxX: 300, maxY: 150 };
  const camera = fitToView(bbox, viewport, 80);
  assert.ok(Math.abs(camera.x - 100) < 1e-9);
  assert.ok(Math.abs(camera.y - 50) < 1e-9);
  const topLeft = worldToScreen(camera, viewport, { x: bbox.minX, y: bbox.minY });
  const bottomRight = worldToScreen(camera, viewport, { x: bbox.maxX, y: bbox.maxY });
  assert.ok(topLeft.x >= 80 - 1e-6 && topLeft.y >= 80 - 1e-6);
  assert.ok(bottomRight.x <= viewport.width - 80 + 1e-6);
  assert.ok(bottomRight.y <= viewport.height - 80 + 1e-6);
});

test("camera: boş kutu varsayılan kameraya döner", () => {
  const camera = fitToView(null, viewport);
  assert.deepEqual(camera, { x: 0, y: 0, zoom: 1 });
});

test("camera: ekran dikdörtgeni dünya sınırına çevrilir", () => {
  const camera: Camera = { x: 0, y: 0, zoom: 2 };
  const world = screenRectToWorld(camera, viewport, { x: 400, y: 300, width: 100, height: 50 });
  assert.ok(Math.abs(world.minX) < 1e-9);
  assert.ok(Math.abs(world.minY) < 1e-9);
  assert.ok(Math.abs(world.maxX - 50) < 1e-9);
  assert.ok(Math.abs(world.maxY - 25) < 1e-9);
});
