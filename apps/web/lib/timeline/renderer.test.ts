/**
 * Çizici duman testi — sahte 2D bağlamıyla çağrıları doğrular.
 * Gerçek canvas gerekmez; katman saf çizim komutları üretir.
 */

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { drawTimeline, readTimelinePalette, rgba, statusTone } from "./renderer.ts";
// @ts-ignore -- Aynı gerekçe.
import { clusterEvents, placeEvents, type TimelineCamera } from "./viewport.ts";
// @ts-ignore -- Aynı gerekçe.
import type { TimelineEvent, TimelineTrack } from "./model.ts";

interface Call {
  method: string;
  args: unknown[];
}

function fakeContext(): { ctx: CanvasRenderingContext2D; calls: Call[] } {
  const calls: Call[] = [];
  const record =
    (method: string) =>
    (...args: unknown[]) => {
      calls.push({ method, args });
    };
  const ctx = {
    canvas: { width: 800, height: 600 },
    save: record("save"),
    restore: record("restore"),
    translate: record("translate"),
    clip: record("clip"),
    rect: record("rect"),
    clearRect: record("clearRect"),
    fillRect: record("fillRect"),
    beginPath: record("beginPath"),
    moveTo: record("moveTo"),
    lineTo: record("lineTo"),
    stroke: record("stroke"),
    fill: record("fill"),
    arc: record("arc"),
    fillText: record("fillText"),
    measureText: (text: string) => ({ width: text.length * 6 }),
    setTransform: record("setTransform"),
    strokeStyle: "",
    fillStyle: "",
    lineWidth: 1,
    font: "",
    textAlign: "left",
    textBaseline: "alphabetic",
  } as unknown as CanvasRenderingContext2D;
  return { ctx, calls };
}

const DAY = 86_400_000;
const base = Date.UTC(2024, 4, 1);

function event(id: string, time: number, track = "system:scan"): TimelineEvent {
  return { id, time, source: "audit", track, trackLabel: track, title: id };
}

const tracks: TimelineTrack[] = [
  { track: "system:scan", label: "Tarama", count: 2 },
  { track: "platform:github", label: "GitHub", count: 1 },
];

test("rgba: kanal dizisini canvas dolgusuna çevirir", () => {
  assert.equal(rgba("250 250 250", 0.5), "rgba(250, 250, 250, 0.5)");
  assert.equal(rgba("#fff", 0.5), "#fff");
  assert.equal(rgba("0 0 0", 5), "rgba(0, 0, 0, 1)");
});

test("statusTone: yalnız bilinen anlam durumları renk alır", () => {
  assert.equal(statusTone("confirmed"), "ok");
  assert.equal(statusTone("candidate"), "warn");
  assert.equal(statusTone("rejected"), "danger");
  assert.equal(statusTone("bilinmeyen"), null);
  assert.equal(statusTone(null), null);
});

test("readTimelinePalette: DOM yoksa varsayılan palet", () => {
  const palette = readTimelinePalette(null);
  assert.equal(palette.ink, "250 250 250");
  assert.match(palette.monoFamily, /mono/);
});

test("drawTimeline: sahneyi çizer ve küme sayısını başa yazar", () => {
  const camera: TimelineCamera = { start: base, end: base + 2 * DAY, scrollY: 0 };
  const viewport = { width: 800, height: 400 };
  const placed = placeEvents(
    [event("a", base), event("a2", base + 1000), event("b", base + DAY, "platform:github")],
    tracks.map((track) => track.track),
    camera,
    viewport
  );
  const clusters = clusterEvents(placed, 14);
  assert.equal(clusters.length, 2);

  const { ctx, calls } = fakeContext();
  drawTimeline(ctx, readTimelinePalette(null), {
    camera,
    viewport,
    gutter: 152,
    tracks,
    clusters,
    selectedEventId: "a",
    hoveredClusterId: clusters[0].id,
  });

  const text = calls
    .filter((call) => call.method === "fillText")
    .map((call) => String(call.args[0]));
  assert.ok(text.includes("Tarama"), "şerit etiketi çizilmeli");
  assert.ok(text.includes("GitHub"), "platform şeridi çizilmeli");
  assert.ok(calls.some((call) => call.method === "arc"), "olay işareti çizilmeli");
  assert.ok(
    calls.some((call) => call.method === "fillRect"),
    "şerit vurgusu çizilmeli"
  );
});

test("drawTimeline: boş pencerede bilgi metni çizer", () => {
  const camera: TimelineCamera = { start: 0, end: 1000, scrollY: 0 };
  const { ctx, calls } = fakeContext();
  drawTimeline(ctx, readTimelinePalette(null), {
    camera,
    viewport: { width: 800, height: 400 },
    gutter: 152,
    tracks,
    clusters: [],
    selectedEventId: null,
    hoveredClusterId: null,
  });
  const text = calls
    .filter((call) => call.method === "fillText")
    .map((call) => String(call.args[0]));
  assert.ok(text.some((value) => value.includes("olay yok")));
});

test("drawTimeline: şerit yokken pencere bilgi metni çizmez", () => {
  const { ctx, calls } = fakeContext();
  drawTimeline(ctx, readTimelinePalette(null), {
    camera: { start: 0, end: 1000, scrollY: 0 },
    viewport: { width: 800, height: 400 },
    gutter: 152,
    tracks: [],
    clusters: [],
    selectedEventId: null,
    hoveredClusterId: null,
  });
  const text = calls
    .filter((call) => call.method === "fillText")
    .map((call) => String(call.args[0]));
  assert.equal(text.some((value) => value.includes("olay yok")), false);
});
