/**
 * Görünüm matematiği testleri (`node --test lib/timeline/*.test.ts`).
 */

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import type { TimelineEvent } from "./model.ts";
// @ts-ignore -- Aynı gerekçe.
import { TRACK_GUTTER, TRACK_HEIGHT, axisTicks, clampScroll, clampToRange, clampWindow, clusterEvents, eventsInWindow, fitCamera, gutterFor, hitTest, padRange, panBy, pickTickStep, placeEvents, clusterRadius, timeRange, timeToX, windowForSpread, windowMs, xToTime, zoomAtRatio } from "./viewport.ts";

const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const base = Date.UTC(2024, 4, 1);
const viewport = { width: 800, height: 400 };

function event(id: string, time: number, track = "system:scan"): TimelineEvent {
  return {
    id,
    time,
    source: "audit",
    track,
    trackLabel: track,
    title: id,
  };
}

test("gutterFor: dar ekranda kolon küçülür, sınırın altına inmez", () => {
  assert.equal(gutterFor(1200), TRACK_GUTTER);
  assert.equal(gutterFor(420), 126);
  assert.equal(gutterFor(200), 96);
  assert.equal(gutterFor(0), 96);
});

test("timeRange: boş liste null, tek olay sıfır genişlik", () => {
  assert.equal(timeRange([]), null);
  assert.deepEqual(timeRange([event("a", 5)]), { start: 5, end: 5 });
  assert.deepEqual(timeRange([event("a", 9), event("b", 3)]), { start: 3, end: 9 });
});

test("padRange: sıfır dolgu tek anı 1 ms pencereye açar", () => {
  assert.deepEqual(padRange({ start: 10, end: 10 }), { start: 10, end: 11 });
  assert.deepEqual(padRange({ start: 0, end: 100 }, 25), { start: -25, end: 125 });
});

test("fitCamera: aralığı dolguyla kapsar", () => {
  const camera = fitCamera({ start: 0, end: 100 });
  assert.ok(camera.start < 0);
  assert.ok(camera.end > 100);
  assert.equal(camera.scrollY, 0);
});

test("zoomAtRatio: çapa oranındaki zaman sabit kalır", () => {
  const camera = { start: 0, end: 1000, scrollY: 0 };
  const zoomed = zoomAtRatio(camera, 2, 0.25);
  assert.equal(zoomed.start + windowMs(zoomed) * 0.25, 250);
  assert.equal(windowMs(zoomed), 500);
});

test("zoomAtRatio: sınırlar pencereyi kıskaçlar", () => {
  const camera = { start: 0, end: 1000, scrollY: 0 };
  const wide = zoomAtRatio(camera, 0.001, 0.5, { min: 10, max: 5000 });
  assert.equal(windowMs(wide), 5000);
  const narrow = zoomAtRatio(camera, 10_000, 0.5, { min: 10, max: 5000 });
  assert.equal(windowMs(narrow), 10);
});

test("clampWindow: pencereyi sınırlar içine çeker", () => {
  const camera = { start: 0, end: 10, scrollY: 0 };
  const clamped = clampWindow(camera, { min: 100, max: 1000 });
  assert.equal(windowMs(clamped), 100);
  assert.equal((clamped.start + clamped.end) / 2, 5);
  assert.equal(clampWindow(camera, { min: 1, max: 1000 }), camera);
});

test("panBy: pencereyi kaydırır, genişliği korur", () => {
  const camera = { start: 0, end: 100, scrollY: 4 };
  const panned = panBy(camera, 50);
  assert.deepEqual(panned, { start: 50, end: 150, scrollY: 4 });
});

test("clampToRange: pencereyi veri çevresinde tutar", () => {
  const range = { start: 0, end: 1000 };
  const left = clampToRange({ start: -5000, end: -4900, scrollY: 0 }, range, 0.5);
  assert.equal(left.start, -50);
  const right = clampToRange({ start: 5000, end: 5100, scrollY: 0 }, range, 0.5);
  assert.equal(right.start, 950);
  const inside = { start: 100, end: 200, scrollY: 0 };
  assert.equal(clampToRange(inside, range), inside);
});

test("timeToX / xToTime: gidiş-dönüş", () => {
  const camera = { start: 1000, end: 3000, scrollY: 0 };
  for (const time of [1000, 1500, 2100, 2999]) {
    const x = timeToX(camera, viewport, time);
    const back = xToTime(camera, viewport, x);
    assert.ok(Math.abs(back - time) < 1e-6);
  }
  assert.equal(timeToX(camera, viewport, 1000), 0);
  assert.equal(timeToX(camera, viewport, 3000), viewport.width);
});

test("placeEvents: şerit indeksine göre y, kameraya göre x verir", () => {
  const camera = { start: base, end: base + DAY, scrollY: 0 };
  const tracks = ["system:scan", "system:pool"];
  const placed = placeEvents(
    [event("a", base, "system:scan"), event("b", base + DAY / 2, "system:pool")],
    tracks,
    camera,
    viewport
  );
  assert.equal(placed[0].x, 0);
  assert.equal(placed[1].x, viewport.width / 2);
  assert.equal(placed[1].y, TRACK_HEIGHT + TRACK_HEIGHT / 2);
});

test("placeEvents: bilinmeyen şerit çizilmez", () => {
  const camera = { start: 0, end: 100, scrollY: 0 };
  const placed = placeEvents([event("a", 50, "system:yok")], ["system:scan"], camera, viewport);
  assert.equal(placed.length, 0);
});

test("clusterEvents: yakın olayları tek kümede toplar", () => {
  const camera = { start: 0, end: 800, scrollY: 0 };
  // Aynı şeritte 0 ve 5 px yakın; 400 px uzak.
  const placed = placeEvents(
    [event("a", 0), event("b", 5), event("c", 400)],
    ["system:scan"],
    camera,
    viewport,
    10
  );
  const clusters = clusterEvents(placed, 14);
  assert.equal(clusters.length, 2);
  assert.equal(clusters[0].size, 2);
  assert.deepEqual(
    clusters[0].events.map((row) => row.id),
    ["a", "b"]
  );
  assert.equal(clusters[1].size, 1);
  assert.equal(clusters[1].id, "c");
});

test("clusterEvents: farklı şeritler karışmaz", () => {
  const camera = { start: 0, end: 800, scrollY: 0 };
  const placed = placeEvents(
    [event("a", 10, "system:scan"), event("b", 12, "system:pool")],
    ["system:scan", "system:pool"],
    camera,
    viewport
  );
  const clusters = clusterEvents(placed, 50);
  assert.equal(clusters.length, 2);
  assert.equal(clusters.every((cluster) => cluster.size === 1), true);
});

test("clusterEvents: yakınlaşınca küme açılır", () => {
  const wide = { start: 0, end: 10 * DAY, scrollY: 0 };
  const near = { start: 0, end: 1000, scrollY: 0 };
  const events = [event("a", 0), event("b", 200)];
  const tracks = ["system:scan"];
  assert.equal(clusterEvents(placeEvents(events, tracks, wide, viewport)).length, 1);
  assert.equal(clusterEvents(placeEvents(events, tracks, near, viewport)).length, 2);
});

test("windowForSpread: küme yayılımından pencere önerir", () => {
  const cluster = {
    id: "c",
    x: 0,
    y: 0,
    trackIndex: 0,
    time: 0,
    events: [],
    size: 3,
    firstTime: 0,
    lastTime: HOUR,
  };
  // 1 saat yayılım, 600 px çizim alanı, 120 px hedef → 5 saatlik pencere.
  assert.equal(windowForSpread(HOUR, 600, 120), 5 * HOUR);
  // Aynı anda olan küme en dar pencereye iner.
  assert.equal(windowForSpread(0, 600, 120), 60_000);
});

test("hitTest: yarıçap içindeki en yakın kümeyi seçer", () => {
  const mk = (id: string, x: number, y: number, size = 1) => ({
    id,
    x,
    y,
    trackIndex: 0,
    time: 0,
    events: [],
    size,
    firstTime: 0,
    lastTime: 0,
  });
  const clusters = [mk("a", 100, 50), mk("b", 104, 50)];
  assert.equal(hitTest(clusters, 103, 50)?.id, "b");
  assert.equal(hitTest(clusters, 400, 50), null);
});

test("clusterRadius: kümeyle büyür, üst sınırı aşmaz", () => {
  assert.equal(clusterRadius(1), 4.5);
  assert.ok(clusterRadius(2) >= 9);
  assert.ok(clusterRadius(9) > clusterRadius(2));
  assert.ok(clusterRadius(10_000) <= 15);
});

test("axisTicks: görünür aralıkta ve etiketli tikler üretir", () => {
  const camera = { start: base, end: base + 6 * DAY, scrollY: 0 };
  const ticks = axisTicks(camera, viewport);
  assert.ok(ticks.length >= 2);
  for (const tick of ticks) {
    assert.ok(tick.time >= camera.start && tick.time <= camera.end);
    assert.ok(tick.label.length > 0);
  }
});

test("axisTicks: yıl sınırını aşınca yılı etikete ekler", () => {
  const camera = { start: Date.UTC(2023, 11, 1), end: Date.UTC(2024, 2, 1), scrollY: 0 };
  const ticks = axisTicks(camera, { width: 800, height: 400 });
  assert.ok(ticks.some((tick) => tick.label.includes("2024")));
});

test("pickTickStep: dar pencerede saniye, geniş pencerede gün", () => {
  assert.ok(pickTickStep(60_000, 800) <= 60_000);
  assert.ok(pickTickStep(365 * DAY, 800) >= DAY);
});

test("eventsInWindow: pencere dışını eler, kenar payı bırakır", () => {
  const camera = { start: 1000, end: 2000, scrollY: 0 };
  const kept = eventsInWindow([event("a", 1000), event("b", 0), event("c", 2000)], camera);
  assert.deepEqual(
    kept.map((row) => row.id),
    ["a", "c"]
  );
});

test("clampScroll: içerik yüksekliğine göre kırpar", () => {
  const camera = { start: 0, end: 1000, scrollY: -5 };
  assert.equal(clampScroll(camera, 3, 100).scrollY, 0);
  const tall = { start: 0, end: 1000, scrollY: 10_000 };
  const maxScroll = 3 * TRACK_HEIGHT - 40;
  assert.equal(clampScroll(tall, 3, 40).scrollY, maxScroll);
  assert.equal(clampScroll(tall, 1, 100).scrollY, 0);
});
