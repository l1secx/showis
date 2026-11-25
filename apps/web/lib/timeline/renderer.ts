/**
 * Canvas2D çizici — zaman ekseni, şeritler ve olay kümeleri.
 *
 * Çizim saf konum kullanır: bileşen kümeleri ölçer, bu katman yalnız boyar.
 * Renkler CSS tokenlarından okunur (`globals.css`), böylece tek siyah tema
 * korunur. Koordinat sistemi: sol `TRACK_GUTTER` px şerit etiketi kolonu,
 * üst `AXIS_HEIGHT` px eksen bandı; kalan alan çizim alanıdır.
 */

// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { AXIS_HEIGHT, TRACK_HEIGHT, axisTicks, clusterRadius, timeToX, type Cluster, type TimelineCamera, type Viewport } from "./viewport.ts";
// @ts-ignore -- Aynı gerekçe.
import { sourceTone } from "./model.ts";

/** `R G B` kanal dizeleri; alfa çağrı anında eklenir. */
export interface TimelinePalette {
  ink: string;
  ink2: string;
  ink3: string;
  ink4: string;
  line: string;
  line2: string;
  accent: string;
  ok: string;
  warn: string;
  danger: string;
  fontFamily: string;
  monoFamily: string;
}

const FALLBACK: TimelinePalette = {
  ink: "250 250 250",
  ink2: "200 200 200",
  ink3: "168 168 168",
  ink4: "124 124 124",
  line: "33 33 33",
  line2: "46 46 46",
  accent: "56 189 248",
  ok: "74 222 128",
  warn: "250 204 21",
  danger: "248 113 113",
  fontFamily: "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif",
  monoFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
};

const TOKEN_KEYS: Array<[keyof TimelinePalette, string]> = [
  ["ink", "--ink"],
  ["ink2", "--ink-2"],
  ["ink3", "--ink-3"],
  ["ink4", "--ink-4"],
  ["line", "--line"],
  ["line2", "--line-2"],
  ["accent", "--accent"],
  ["ok", "--ok"],
  ["warn", "--warn"],
  ["danger", "--danger"],
  ["fontFamily", "--font-sans"],
  ["monoFamily", "--font-mono"],
];

/** Kök elemendan tema tokenlarını okur; SSR'de yalnız varsayılan döner. */
export function readTimelinePalette(root?: HTMLElement | null): TimelinePalette {
  if (typeof window === "undefined" || !root) return FALLBACK;
  const style = window.getComputedStyle(root);
  const palette: TimelinePalette = { ...FALLBACK };
  for (const [key, token] of TOKEN_KEYS) {
    const value = style.getPropertyValue(token).replace(/\s+/g, " ").trim();
    if (value) palette[key] = value;
  }
  return palette;
}

/** `R G B` + alfa → canvas dolgusu. */
export function rgba(channels: string, alpha = 1): string {
  const parts = channels.trim().split(/\s+/);
  if (parts.length !== 3) return channels;
  return `rgba(${parts.join(", ")}, ${Math.min(1, Math.max(0, alpha))})`;
}

const STATUS_TONE: Record<string, "ok" | "warn" | "danger"> = {
  confirmed: "ok",
  candidate: "warn",
  lead: "warn",
  proposed: "warn",
  rejected: "danger",
};

/** Durum rengi; bilinmeyen veya durumsuz `null` (nötr çizim). */
export function statusTone(status: string | null | undefined): "ok" | "warn" | "danger" | null {
  if (!status) return null;
  return STATUS_TONE[status] ?? null;
}

export interface DrawInput {
  camera: TimelineCamera;
  viewport: Viewport;
  /** Sol etiket kolonunun genişliği; dar ekranda küçülür. */
  gutter: number;
  tracks: ReadonlyArray<{ track: string; label: string; count: number }>;
  clusters: readonly Cluster[];
  selectedEventId: string | null;
  hoveredClusterId: string | null;
}

/** Eksen bandı: etiketler ve dikey kılavuz çizgiler. */
function drawAxis(
  ctx: CanvasRenderingContext2D,
  palette: TimelinePalette,
  camera: TimelineCamera,
  input: DrawInput,
  plotWidth: number
): void {
  const ticks = axisTicks(camera, { width: plotWidth, height: input.viewport.height });
  ctx.save();
  ctx.translate(input.gutter, 0);

  ctx.strokeStyle = rgba(palette.line2, 0.6);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, AXIS_HEIGHT - 0.5);
  ctx.lineTo(plotWidth, AXIS_HEIGHT - 0.5);
  ctx.stroke();

  ctx.font = `12px ${palette.fontFamily}`;
  ctx.textBaseline = "middle";
  for (const tick of ticks) {
    const x = timeToX(camera, { width: plotWidth, height: 0 }, tick.time);
    if (x < -60 || x > plotWidth + 60) continue;

    ctx.strokeStyle = rgba(palette.line, 0.5);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(Math.round(x) + 0.5, AXIS_HEIGHT - 6);
    ctx.lineTo(Math.round(x) + 0.5, AXIS_HEIGHT);
    ctx.stroke();

    const label = tick.label;
    const width = ctx.measureText(label).width;
    const left = Math.min(plotWidth - width - 2, Math.max(2, x - width / 2));
    ctx.fillStyle = rgba(palette.ink3, 1);
    ctx.fillText(label, left, AXIS_HEIGHT / 2 - 4);
  }
  ctx.restore();
}

/** Sol kolon: şerit adı ve olay sayısı. */
function drawGutter(
  ctx: CanvasRenderingContext2D,
  palette: TimelinePalette,
  input: DrawInput
): void {
  const scroll = input.camera.scrollY;
  ctx.save();
  ctx.translate(0, AXIS_HEIGHT);
  ctx.beginPath();
  ctx.rect(0, 0, input.gutter, input.viewport.height);
  ctx.clip();

  ctx.textBaseline = "middle";
  input.tracks.forEach((track, index) => {
    const y = index * TRACK_HEIGHT - scroll + TRACK_HEIGHT / 2;
    if (y < -TRACK_HEIGHT || y > input.viewport.height + TRACK_HEIGHT) return;

    ctx.font = `12px ${palette.monoFamily}`;
    const count = String(track.count);
    const countWidth = ctx.measureText(count).width;
    ctx.fillStyle = rgba(palette.ink4, 1);
    ctx.textAlign = "right";
    ctx.fillText(count, input.gutter - 12, y);
    ctx.textAlign = "left";

    ctx.font = `12px ${palette.fontFamily}`;
    ctx.fillStyle = rgba(palette.ink2, 1);
    const maxWidth = Math.max(24, input.gutter - 24 - countWidth - 10);
    let label = track.label;
    while (label.length > 1 && ctx.measureText(`${label}…`).width > maxWidth) {
      label = label.slice(0, -1);
    }
    if (label !== track.label) label = `${label}…`;
    // Etiket alanı: 12 px sol dolgu, sağda sayı için yer.
    ctx.fillText(label, 12, y);
  });
  ctx.restore();
}

/** Çizim alanı: şerit satırları, küme işaretleri. */
function drawPlot(
  ctx: CanvasRenderingContext2D,
  palette: TimelinePalette,
  input: DrawInput,
  plotWidth: number
): void {
  const scroll = input.camera.scrollY;
  const hoveredTrack = input.clusters.find(
    (cluster) => cluster.id === input.hoveredClusterId
  )?.trackIndex;
  const ticks = axisTicks(input.camera, { width: plotWidth, height: input.viewport.height });

  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, plotWidth, input.viewport.height);
  ctx.clip();

  // Dikey kılavuzlar (eksen etiketleriyle hizalı).
  ctx.strokeStyle = rgba(palette.line, 0.35);
  ctx.lineWidth = 1;
  for (const tick of ticks) {
    const x = Math.round(timeToX(input.camera, { width: plotWidth, height: 0 }, tick.time)) + 0.5;
    if (x < 0 || x > plotWidth) continue;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, input.viewport.height);
    ctx.stroke();
  }

  // Şerit satırları.
  input.tracks.forEach((_, index) => {
    const y = index * TRACK_HEIGHT - scroll;
    if (y + TRACK_HEIGHT < 0 || y > input.viewport.height) return;
    if (index === hoveredTrack) {
      ctx.fillStyle = rgba(palette.ink, 0.03);
      ctx.fillRect(0, y, plotWidth, TRACK_HEIGHT);
    }
    ctx.strokeStyle = rgba(palette.line, 0.55);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, Math.round(y) + 0.5);
    ctx.lineTo(plotWidth, Math.round(y) + 0.5);
    ctx.stroke();
  });
  const bottom = input.tracks.length * TRACK_HEIGHT - scroll;
  if (bottom >= 0 && bottom <= input.viewport.height) {
    ctx.strokeStyle = rgba(palette.line, 0.55);
    ctx.beginPath();
    ctx.moveTo(0, Math.round(bottom) + 0.5);
    ctx.lineTo(plotWidth, Math.round(bottom) + 0.5);
    ctx.stroke();
  }

  // Kümeler.
  for (const cluster of input.clusters) {
    const centerY = cluster.y;
    if (centerY < -TRACK_HEIGHT || centerY > input.viewport.height + TRACK_HEIGHT) continue;
    const x = cluster.x;
    const radius = clusterRadius(cluster.size);
    const source = cluster.events[0]?.source ?? "audit";
    const tone = sourceTone(source);
    const isSelected = cluster.events.some((event) => event.id === input.selectedEventId);
    const isHovered = cluster.id === input.hoveredClusterId;
    const status = cluster.events.length === 1 ? cluster.events[0].status : null;
    const statusColor = statusTone(status);

    if (statusColor) {
      ctx.strokeStyle = rgba(palette[statusColor], 0.9);
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.arc(x, centerY, radius + 2.5, 0, Math.PI * 2);
      ctx.stroke();
    }

    if (isHovered) {
      ctx.fillStyle = rgba(palette.ink, 0.09);
      ctx.beginPath();
      ctx.arc(x, centerY, radius + 6, 0, Math.PI * 2);
      ctx.fill();
    }

    if (cluster.size > 1) {
      // Küme: siyah iç, şerit renginde halka, ortada sayı.
      ctx.fillStyle = "#000000";
      ctx.beginPath();
      ctx.arc(x, centerY, radius, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = rgba(tone, 0.95);
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.arc(x, centerY, radius, 0, Math.PI * 2);
      ctx.stroke();

      if (radius >= 9) {
        ctx.font = `12px ${palette.monoFamily}`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillStyle = rgba(tone, 1);
        ctx.fillText(String(cluster.size), x, centerY + 0.5);
        ctx.textAlign = "left";
      }
    } else {
      ctx.fillStyle = rgba(tone, 1);
      ctx.beginPath();
      ctx.arc(x, centerY, radius, 0, Math.PI * 2);
      ctx.fill();
    }

    if (isSelected) {
      ctx.strokeStyle = rgba(palette.accent, 1);
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.arc(x, centerY, radius + 4.5, 0, Math.PI * 2);
      ctx.stroke();
    }
  }

  if (input.clusters.length === 0 && input.tracks.length > 0) {
    ctx.font = `13px ${palette.fontFamily}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = rgba(palette.ink3, 1);
    ctx.fillText(
      "Bu zaman penceresinde olay yok — kaydırın veya yakınlaştırın.",
      plotWidth / 2,
      input.viewport.height / 2
    );
    ctx.textAlign = "left";
  }
  ctx.restore();
}

/**
 * Tüm sahneyi çizer. Tuval boyutu CSS pikselidir; DPR ölçeklemesi çağıranda
 * `ctx.setTransform` ile uygulanır.
 */
export function drawTimeline(
  ctx: CanvasRenderingContext2D,
  palette: TimelinePalette,
  input: DrawInput
): void {
  const plotWidth = Math.max(1, input.viewport.width - input.gutter);
  const totalHeight = AXIS_HEIGHT + input.viewport.height;
  ctx.clearRect(0, 0, input.viewport.width, totalHeight);

  drawAxis(ctx, palette, input.camera, input, plotWidth);
  drawGutter(ctx, palette, input);

  ctx.save();
  ctx.translate(input.gutter, AXIS_HEIGHT);
  drawPlot(ctx, palette, input, plotWidth);
  ctx.restore();

  // Kolon ayracı.
  ctx.strokeStyle = rgba(palette.line, 0.9);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(input.gutter + 0.5, 0);
  ctx.lineTo(input.gutter + 0.5, totalHeight);
  ctx.stroke();
}
