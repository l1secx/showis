/**
 * Mini harita dönüşümleri ve Canvas2D çizimi. Dönüşüm fonksiyonları saftır;
 * çizim yalnız verilen bağlam üzerinde çalışır, DOM bilgisi istemez.
 */

// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { screenToWorld, worldToScreen } from "./camera.ts";
// @ts-ignore -- Aynı gerekçe.
import type { BBox, Camera, Rect, Size, Theme } from "./types.ts";

export const MINIMAP_DEFAULT_SIZE: Size = { width: 168, height: 112 };
export const MINIMAP_PADDING = 8;

export interface MinimapTransform {
  /** Dünya biriminden mini harita pikseline ölçek. */
  scale: number;
  /** Dünya `minX/minY` noktasının mini haritadaki piksel konumu. */
  offsetX: number;
  offsetY: number;
}

/**
 * Dünya sınır kutusunu mini haritaya sığdırır. Boş kutu `null` döner.
 * Ölçek iki eksende aynıdır; harita bozulmaz.
 */
export function minimapTransform(
  bbox: BBox | null,
  size: Size,
  padding = MINIMAP_PADDING
): MinimapTransform | null {  if (!bbox) return null;
  const width = Math.max(1, size.width - padding * 2);
  const height = Math.max(1, size.height - padding * 2);
  const spanX = Math.max(bbox.maxX - bbox.minX, 1e-6);
  const spanY = Math.max(bbox.maxY - bbox.minY, 1e-6);
  const scale = Math.min(width / spanX, height / spanY);
  const offsetX = padding + (width - spanX * scale) / 2 - bbox.minX * scale;
  const offsetY = padding + (height - spanY * scale) / 2 - bbox.minY * scale;
  return { scale, offsetX, offsetY };
}

/** Dünya noktasını mini harita pikseline çevirir. */
export function worldToMinimap(
  transform: MinimapTransform,
  point: { x: number; y: number }
): { x: number; y: number } {
  return {
    x: transform.offsetX + point.x * transform.scale,
    y: transform.offsetY + point.y * transform.scale,
  };
}

/** Mini harita pikselini dünya noktasına çevirir (tıkla-git). */
export function minimapToWorld(
  transform: MinimapTransform,
  point: { x: number; y: number }
): { x: number; y: number } {
  const scale = transform.scale > 0 ? transform.scale : 1;
  return {
    x: (point.x - transform.offsetX) / scale,
    y: (point.y - transform.offsetY) / scale,
  };
}

/**
 * Görünüm dikdörtgeni: kameranın dünya uzayındaki pencere sınırı, mini
 * harita pikselinde. Panellerde çizilir; tıklama bu dikdörtgeni ortalara taşır.
 */
export function minimapViewRect(
  camera: Camera,
  viewport: Size,
  transform: MinimapTransform
): Rect {
  const topLeft = screenToWorld(camera, viewport, { x: 0, y: 0 });
  const bottomRight = screenToWorld(camera, viewport, {
    x: viewport.width,
    y: viewport.height,
  });
  const start = worldToMinimap(transform, topLeft);
  const end = worldToMinimap(transform, bottomRight);
  return {
    x: Math.min(start.x, end.x),
    y: Math.min(start.y, end.y),
    width: Math.abs(end.x - start.x),
    height: Math.abs(end.y - start.y),
  };
}

/** Tıklanan mini harita noktasını kameranın merkezi yapan kamera. */
export function cameraForMinimapPoint(
  camera: Camera,
  transform: MinimapTransform,
  point: { x: number; y: number }
): Camera {
  const world = minimapToWorld(transform, point);
  return { x: world.x, y: world.y, zoom: camera.zoom };
}

export interface MinimapNode {
  x: number;
  y: number;
  /** Verilmezse tüm düğümler aynı noktayla çizilir. */
  color?: string;
  radius?: number;
}

export interface MinimapDrawOptions {
  /** Çizilecek düğümlerin dünya konumları. */
  points: readonly MinimapNode[];
  camera: Camera;
  viewport: Size;
  size: Size;
  theme: Theme;
  /** Mini haritanın sol üst köşesi (CSS piksel). */
  origin?: { x: number; y: number };
  padding?: number;
}

export interface MinimapDrawResult {
  /** Görünüm dikdörtgeni, mini harita yerel pikseli. */
  viewRect: Rect;
  transform: MinimapTransform | null;
}

/**
 * Mini haritayı verilen bağlama çizer: siyah zemin, 1px çizgi, soluk
 * düğüm noktaları ve aksan görünüm dikdörtgeni. Çizim yalnız `ctx`
 * üzerinde çalışır; test dönüşümü doğrular.
 */
export function drawMinimap(
  ctx: CanvasRenderingContext2D,
  options: MinimapDrawOptions
): MinimapDrawResult {
  const { points, camera, viewport, size, theme } = options;
  const origin = options.origin ?? { x: 0, y: 0 };
  const padding = options.padding ?? MINIMAP_PADDING;

  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const point of points) {
    if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) continue;
    minX = Math.min(minX, point.x);
    minY = Math.min(minY, point.y);
    maxX = Math.max(maxX, point.x);
    maxY = Math.max(maxY, point.y);
  }
  const bbox =
    Number.isFinite(minX) && Number.isFinite(minY)
      ? { minX, minY, maxX, maxY }
      : null;
  const transform = minimapTransform(bbox, size, padding);

  ctx.save();
  ctx.globalAlpha = 1;
  ctx.fillStyle = theme.background;
  ctx.fillRect(origin.x, origin.y, size.width, size.height);
  ctx.strokeStyle = theme.edge;
  ctx.lineWidth = 1;
  ctx.strokeRect(origin.x + 0.5, origin.y + 0.5, size.width - 1, size.height - 1);

  if (!transform) {
    ctx.restore();
    return { viewRect: { x: 0, y: 0, width: 0, height: 0 }, transform: null };
  }

  ctx.fillStyle = theme.labelMuted;
  for (const point of points) {
    const mini = worldToMinimap(transform, point);
    const radius = Math.max(0.6, Math.min(2.2, point.radius ?? 1.1));
    ctx.fillStyle = point.color ?? theme.labelMuted;
    ctx.beginPath();
    ctx.arc(origin.x + mini.x, origin.y + mini.y, radius, 0, Math.PI * 2);
    ctx.fill();
  }

  const viewRect = minimapViewRect(camera, viewport, transform);
  ctx.fillStyle = theme.cluster;
  ctx.fillRect(
    origin.x + viewRect.x,
    origin.y + viewRect.y,
    viewRect.width,
    viewRect.height
  );
  ctx.strokeStyle = theme.accent;
  ctx.lineWidth = 1.2;
  ctx.strokeRect(
    origin.x + viewRect.x + 0.5,
    origin.y + viewRect.y + 0.5,
    Math.max(1, viewRect.width - 1),
    Math.max(1, viewRect.height - 1)
  );
  ctx.restore();
  return { viewRect, transform };
}

/** Ekran noktası mini harita kutusunda mı? */
export function pointInMinimap(
  point: { x: number; y: number },
  origin: { x: number; y: number },
  size: Size
): boolean {
  return (
    point.x >= origin.x &&
    point.x <= origin.x + size.width &&
    point.y >= origin.y &&
    point.y <= origin.y + size.height
  );
}

/** Mini haritanın tuval içindeki yeri: sağ alt köşe, sabit kenar boşluğu. */
export function minimapOrigin(
  viewport: Size,
  size: Size = MINIMAP_DEFAULT_SIZE,
  margin = 12
): { x: number; y: number } {
  return {
    x: Math.max(0, viewport.width - size.width - margin),
    y: Math.max(0, viewport.height - size.height - margin),
  };
}

/**
 * Tuval noktası mini haritaya düşüyorsa dünya merkezini döndürür; düşmüyorsa
 * `null`. Tıkla-git için kullanılır.
 */
export function minimapTarget(
  screen: { x: number; y: number },
  nodes: readonly { x: number; y: number }[],
  camera: Camera,
  viewport: Size,
  size: Size = MINIMAP_DEFAULT_SIZE,
  margin = 12
): { x: number; y: number } | null {
  const origin = minimapOrigin(viewport, size, margin);
  if (!pointInMinimap(screen, origin, size)) return null;
  let bbox = null as BBox | null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const node of nodes) {
    if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) continue;
    minX = Math.min(minX, node.x);
    minY = Math.min(minY, node.y);
    maxX = Math.max(maxX, node.x);
    maxY = Math.max(maxY, node.y);
  }
  if (Number.isFinite(minX) && Number.isFinite(minY)) bbox = { minX, minY, maxX, maxY };
  const transform = minimapTransform(bbox, size);
  if (!transform) return null;
  const local = { x: screen.x - origin.x, y: screen.y - origin.y };
  const world = minimapToWorld(transform, local);
  void camera;
  return world;
}

/** Dünya noktasının dünya uzayındaki ekran karşılığı; yardımcı. */
export function miniScreenPoint(
  camera: Camera,
  viewport: Size,
  point: { x: number; y: number }
): { x: number; y: number } {
  return worldToScreen(camera, viewport, point);
}
