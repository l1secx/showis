/**
 * Zaman çizelgesi görünüm matematiği — saf fonksiyonlar, DOM yok.
 *
 * Eksen bir "piksel/saniye" ölçeğinden değil, görünür zaman penceresinden
 * türetilir: kamera sol kenarı `start`, sağ kenarı `end`'dir. Yakınlaştırma
 * pencereyi daraltır; kümeleme ekran uzayında yapılır ki yakınlaşınca
 * kümeler kendiliğinden açılsın.
 */

// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import type { TimelineEvent } from "./model.ts";

/** Görünür zaman penceresi (epoch ms) + şerit kaydırma. */
export interface TimelineCamera {
  /** Sol kenarın zamanı (ms). */
  start: number;
  /** Sağ kenarın zamanı (ms). */
  end: number;
  /** Dikey kaydırma (şerit yüksekliği birimi). */
  scrollY: number;
}

export interface Viewport {
  width: number;
  height: number;
}

export interface TimeRange {
  start: number;
  end: number;
}

/** Eksen etiketi biçimi. */
export type TickUnit = "second" | "minute" | "hour" | "day" | "month" | "year";

export interface AxisTick {
  time: number;
  label: string;
  /** Ana çizgi mi (alt birim çizgileri ince kalır). */
  major: boolean;
}

/** Şerit yüksekliği: CSS piksel. */
export const TRACK_HEIGHT = 30;
/** Sol etiket kolonunun üst sınırı (CSS piksel). */
export const TRACK_GUTTER = 152;

/**
 * Dar ekranda etiket kolonu küçülür: dar telefonda 152 px kolon çizim
 * alanını yutuyordu. Genişlikle orantılı, 96 px altına inmez.
 */
export function gutterFor(width: number): number {
  const safe = Number.isFinite(width) ? width : 0;
  if (safe >= 900) return TRACK_GUTTER;
  return Math.max(96, Math.min(TRACK_GUTTER, Math.round(safe * 0.3)));
}
/** Eksen bandının yüksekliği (CSS piksel). */
export const AXIS_HEIGHT = 30;
/** Tek olay işaretinin yarıçapı (CSS piksel). */
export const EVENT_RADIUS = 4.5;
/** Ekran uzayında bu mesafeden yakın olaylar tek kümede toplanır. */
export const CLUSTER_PX = 14;
/** Azami yakınlaştırma: görünür pencere alt sınırı (ms). */
export const MIN_WINDOW_MS = 60_000;
/** Asgari yakınlaştırma: veri aralığının en fazla bu katı görünür. */
export const MAX_WINDOW_FACTOR = 8;

function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, value));
}

const SECOND = 1000;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** Verinin zaman aralığı; boş liste `null`. */
export function timeRange(events: readonly TimelineEvent[]): TimeRange | null {
  let min = Infinity;
  let max = -Infinity;
  for (const event of events) {
    if (!Number.isFinite(event.time)) continue;
    if (event.time < min) min = event.time;
    if (event.time > max) max = event.time;
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
  return { start: min, end: max };
}

/** Aralığı en az `padMs` kadar genişletir; tek anlık veri de görünür pencere alır. */
export function padRange(range: TimeRange, padMs = 0): TimeRange {
  const pad = Math.max(0, padMs);
  const start = range.start - pad;
  let end = range.end + pad;
  if (end - start < 1) end = start + 1;
  return { start, end };
}

export function windowMs(camera: TimelineCamera): number {
  return Math.max(1, camera.end - camera.start);
}

/** Görünür pencereye sığdırılmış kamera; veri aralığının %5'i dolgu. */
export function fitCamera(range: TimeRange, factor = 1.05): TimelineCamera {
  const span = Math.max(1, range.end - range.start);
  const pad = (span * (Math.max(1, factor) - 1)) / 2;
  return { start: range.start - pad, end: range.end + pad, scrollY: 0 };
}

export const MIN_WINDOW = MIN_WINDOW_MS;

/** Yakınlaştırma sınırları: veri aralığı `range` genişliğine göre. */
export function zoomBounds(range: TimeRange): { min: number; max: number } {
  const span = Math.max(1, range.end - range.start);
  return { min: Math.min(MIN_WINDOW_MS, span), max: span * MAX_WINDOW_FACTOR };
}

export function clampWindow(camera: TimelineCamera, bounds: { min: number; max: number }): TimelineCamera {
  const span = windowMs(camera);
  const clamped = Math.min(bounds.max, Math.max(bounds.min, span));
  if (clamped === span) return camera;
  const center = (camera.start + camera.end) / 2;
  return { start: center - clamped / 2, end: center + clamped / 2, scrollY: camera.scrollY };
}

/** `anchor` ekran x oranında (0..1) sabit kalacak biçimde pencereyi ölçekler. */
export function zoomAtRatio(
  camera: TimelineCamera,
  factor: number,
  anchorRatio = 0.5,
  bounds?: { min: number; max: number }
): TimelineCamera {
  const span = windowMs(camera);
  const anchor = camera.start + span * Math.min(1, Math.max(0, anchorRatio));
  let nextSpan = span / (Number.isFinite(factor) && factor > 0 ? factor : 1);
  if (bounds) nextSpan = Math.min(bounds.max, Math.max(bounds.min, nextSpan));
  const start = anchor - nextSpan * Math.min(1, Math.max(0, anchorRatio));
  return { start, end: start + nextSpan, scrollY: camera.scrollY };
}

/** Pencereyi `deltaMs` kaydırır (delta > 0 ileri). */
export function panBy(camera: TimelineCamera, deltaMs: number): TimelineCamera {
  const shift = Number.isFinite(deltaMs) ? deltaMs : 0;
  return { start: camera.start + shift, end: camera.end + shift, scrollY: camera.scrollY };
}

/**
 * Görünür pencereyi veri aralığına yakın tutar: pencere en az yarısı kadar
 * veriyle örtüşür. Sonsuz boşluğa kaymayı engeller.
 */
export function clampToRange(
  camera: TimelineCamera,
  range: TimeRange,
  slack = 0.5
): TimelineCamera {
  const span = windowMs(camera);
  const pad = span * Math.max(0, slack);
  const minStart = range.start - pad;
  const maxStart = range.end + pad - span;
  const start = Math.min(maxStart, Math.max(minStart, camera.start));
  return start === camera.start ? camera : { start, end: start + span, scrollY: camera.scrollY };
}

/** Zamanı ekran x'ine çevirir (şerit alanı içinde). */
export function timeToX(camera: TimelineCamera, viewport: Viewport, time: number): number {
  const span = windowMs(camera);
  return ((time - camera.start) / span) * viewport.width;
}

/** Ekran x'ini zamana çevirir. */
export function xToTime(camera: TimelineCamera, viewport: Viewport, x: number): number {
  const span = windowMs(camera);
  return camera.start + (x / Math.max(1, viewport.width)) * span;
}

/** Ham çizim konumu (gutter hariç); şerit alanına kırpma çağıranın işi. */
export interface PlacedEvent {
  event: TimelineEvent;
  x: number;
  trackIndex: number;
  y: number;
}

export function placeEvents(
  events: readonly TimelineEvent[],
  tracks: readonly string[],
  camera: TimelineCamera,
  viewport: Viewport,
  trackHeight = TRACK_HEIGHT
): PlacedEvent[] {
  const index = new Map<string, number>();
  tracks.forEach((track, i) => index.set(track, i));
  const placed: PlacedEvent[] = [];
  for (const event of events) {
    const trackIndex = index.get(event.track);
    if (trackIndex === undefined) continue;
    const x = timeToX(camera, viewport, event.time);
    // `y` şerit satırının merkezidir; çizici ve isabet testi aynı değeri kullanır.
    const y = trackIndex * trackHeight - camera.scrollY + trackHeight / 2;
    placed.push({ event, x, trackIndex, y });
  }
  return placed;
}

/** Ekranda çizilen küme: tek olay `size === 1`. */
export interface Cluster {
  id: string;
  x: number;
  y: number;
  trackIndex: number;
  time: number;
  events: TimelineEvent[];
  /** Kümedeki en büyük değer; işaret yarıçapını büyütür. */
  size: number;
  /** Kümede uç (en eski/en yeni) zamanlar. */
  firstTime: number;
  lastTime: number;
}

/**
 * Ekran uzayında yakın olayları kümeler. Aynı şeritte `CLUSTER_PX` içinde
 * kalan ardışık olaylar tek kümeye girer. Kümeleme yakınlaştıkça açılır:
 * `x` ekran konumundan türetilir, dünya konumundan değil.
 */
export function clusterEvents(
  placed: readonly PlacedEvent[],
  clusterPx = CLUSTER_PX
): Cluster[] {
  const byTrack = new Map<string, PlacedEvent[]>();
  for (const item of placed) {
    const key = `${item.trackIndex}`;
    const rows = byTrack.get(key);
    if (rows) rows.push(item);
    else byTrack.set(key, [item]);
  }

  const clusters: Cluster[] = [];
  for (const rows of byTrack.values()) {
    rows.sort((a, b) => a.x - b.x || a.event.id.localeCompare(b.event.id));
    let bucket: PlacedEvent[] = [];
    let bucketStart = 0;
    for (const row of rows) {
      if (bucket.length === 0) {
        bucket = [row];
        bucketStart = row.x;
        continue;
      }
      if (row.x - bucketStart <= clusterPx) {
        bucket.push(row);
        continue;
      }
      clusters.push(toCluster(bucket));
      bucket = [row];
      bucketStart = row.x;
    }
    if (bucket.length > 0) clusters.push(toCluster(bucket));
  }

  return clusters.sort(
    (a, b) => a.trackIndex - b.trackIndex || a.time - b.time || a.id.localeCompare(b.id)
  );
}

function toCluster(rows: PlacedEvent[]): Cluster {
  const first = rows[0];
  let sumX = 0;
  let firstTime = Infinity;
  let lastTime = -Infinity;
  for (const row of rows) {
    sumX += row.x;
    if (row.event.time < firstTime) firstTime = row.event.time;
    if (row.event.time > lastTime) lastTime = row.event.time;
  }
  return {
    id: rows.length === 1 ? rows[0].event.id : `cluster:${first.event.id}`,
    x: sumX / rows.length,
    y: first.y,
    trackIndex: first.trackIndex,
    time: (firstTime + lastTime) / 2,
    events: rows.map((row) => row.event),
    size: rows.length,
    firstTime,
    lastTime,
  };
}

/**
 * Kümenin açılması için gereken yakınlaştırma penceresi (ms): kümedeki uçlar
 * `targetPx` ekran pikseli kadar ayrışsın. `spread = 0` → en dar pencere.
 */
export function windowForSpread(
  spreadMs: number,
  plotWidth: number,
  targetPx = 120
): number {
  const width = Math.max(1, plotWidth);
  const target = Math.max(1, targetPx);
  const spread = Math.max(0, spreadMs);
  return Math.max(MIN_WINDOW_MS, (spread * width) / target);
}


/**
 * Küme işaretinin ekran yarıçapı; tek olay `EVENT_RADIUS`. Küme en az 9 px
 * yarıçapa çıkar: içine olay sayısı sığsın ve tek işaretten ayrışsın.
 */
export function clusterRadius(size: number): number {
  if (size <= 1) return EVENT_RADIUS;
  return clamp(9 + Math.log2(size) * 1.6, 9, 15);
}

/** Ekran noktasına en yakın küme; `radius` toleransı dışındakiler seçilmez. */
export function hitTest(
  clusters: readonly Cluster[],
  x: number,
  y: number,
  radius = 10
): Cluster | null {
  let best: Cluster | null = null;
  let bestDistance = Infinity;
  for (const cluster of clusters) {
    const dx = cluster.x - x;
    const dy = cluster.y - y;
    const distance = Math.sqrt(dx * dx + dy * dy);
    const threshold = radius + clusterRadius(cluster.size);
    if (distance <= threshold && distance < bestDistance) {
      best = cluster;
      bestDistance = distance;
    }
  }
  return best;
}

const MONTHS_TR = [
  "Oca",
  "Şub",
  "Mar",
  "Nis",
  "May",
  "Haz",
  "Tem",
  "Ağu",
  "Eyl",
  "Eki",
  "Kas",
  "Ara",
];

function two(value: number): string {
  return value < 10 ? `0${value}` : String(value);
}

function tickLabel(time: number, unit: TickUnit, showYear: boolean): string {
  const date = new Date(time);
  switch (unit) {
    case "second":
      return `${two(date.getHours())}:${two(date.getMinutes())}:${two(date.getSeconds())}`;
    case "minute":
      return `${two(date.getHours())}:${two(date.getMinutes())}`;
    case "hour":
      return `${two(date.getHours())}:00`;
    case "day":
      return `${date.getDate()} ${MONTHS_TR[date.getMonth()]}${showYear ? ` ${date.getFullYear()}` : ""}`;
    case "month":
      return `${MONTHS_TR[date.getMonth()]}${showYear ? ` ${date.getFullYear()}` : ""}`;
    case "year":
      return String(date.getFullYear());
    default:
      return "";
  }
}

const TICK_STEPS: Array<{ unit: TickUnit; ms: number }> = [
  { unit: "second", ms: SECOND },
  { unit: "second", ms: 5 * SECOND },
  { unit: "second", ms: 15 * SECOND },
  { unit: "second", ms: 30 * SECOND },
  { unit: "minute", ms: MINUTE },
  { unit: "minute", ms: 5 * MINUTE },
  { unit: "minute", ms: 15 * MINUTE },
  { unit: "minute", ms: 30 * MINUTE },
  { unit: "hour", ms: HOUR },
  { unit: "hour", ms: 3 * HOUR },
  { unit: "hour", ms: 6 * HOUR },
  { unit: "hour", ms: 12 * HOUR },
  { unit: "day", ms: DAY },
  { unit: "day", ms: 2 * DAY },
  { unit: "day", ms: 3 * DAY },
  { unit: "day", ms: 7 * DAY },
  { unit: "day", ms: 14 * DAY },
  { unit: "month", ms: 30 * DAY },
  { unit: "month", ms: 90 * DAY },
  { unit: "month", ms: 180 * DAY },
  { unit: "year", ms: 365 * DAY },
  { unit: "year", ms: 5 * 365 * DAY },
  { unit: "year", ms: 10 * 365 * DAY },
];

/** Etiket çakışmasını önlemek için hedef adım: ~90 pikselde bir tik. */
export function pickTickStep(spanMs: number, widthPx: number, minPxPerTick = 90): number {
  const target = Math.max(1, (spanMs / Math.max(1, widthPx)) * minPxPerTick);
  for (const step of TICK_STEPS) {
    if (step.ms >= target) return step.ms;
  }
  const last = TICK_STEPS[TICK_STEPS.length - 1];
  return Math.ceil(target / last.ms) * last.ms;
}

function unitForStep(stepMs: number): TickUnit {
  for (const step of TICK_STEPS) {
    if (step.ms === stepMs) return step.unit;
  }
  if (stepMs % (365 * DAY) === 0) return "year";
  if (stepMs % (30 * DAY) === 0) return "month";
  if (stepMs % DAY === 0) return "day";
  if (stepMs % HOUR === 0) return "hour";
  if (stepMs % MINUTE === 0) return "minute";
  return "second";
}

/** Görünür penceredeki ana/ara eksen tikleri. */
export function axisTicks(
  camera: TimelineCamera,
  viewport: Viewport,
  minPxPerTick = 90
): AxisTick[] {
  const span = windowMs(camera);
  const step = pickTickStep(span, viewport.width, minPxPerTick);
  const unit = unitForStep(step);
  const first = Math.ceil(camera.start / step) * step;
  const showYear = new Date(camera.start).getFullYear() !== new Date(camera.end).getFullYear();
  const ticks: AxisTick[] = [];
  for (let time = first; time <= camera.end; time += step) {
    ticks.push({
      time,
      label: tickLabel(time, unit, showYear),
      major: true,
    });
  }
  return ticks;
}

/** Görünür aralığa kırpılmış olaylar (çizim dışı bırakılır). */
export function eventsInWindow(
  events: readonly TimelineEvent[],
  camera: TimelineCamera
): TimelineEvent[] {
  const pad = windowMs(camera) * 0.02;
  return events.filter((event) => event.time >= camera.start - pad && event.time <= camera.end + pad);
}

/** Şerit panelinin toplam içerik yüksekliği. */
export function tracksHeight(trackCount: number, trackHeight = TRACK_HEIGHT): number {
  return Math.max(0, trackCount) * trackHeight;
}

/** Dikey kaydırmayı içerik sınırına oturtur. */
export function clampScroll(
  camera: TimelineCamera,
  trackCount: number,
  viewportHeight: number,
  trackHeight = TRACK_HEIGHT
): TimelineCamera {
  const maxScroll = Math.max(0, tracksHeight(trackCount, trackHeight) - Math.max(0, viewportHeight));
  const scrollY = Math.min(maxScroll, Math.max(0, camera.scrollY));
  return scrollY === camera.scrollY ? camera : { ...camera, scrollY };
}
