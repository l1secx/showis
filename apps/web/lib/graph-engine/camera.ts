/**
 * Kamera dönüşümleri. Saf fonksiyonlar; DOM veya canvas gerektirmez.
 *
 * Ekran uzayı CSS pikselidir (devicePixelRatio renderer katmanında ele alınır).
 */

// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { clamp, type BBox, type Camera, type Point, type Size } from "./types.ts";

export const MIN_ZOOM = 0.05;
export const MAX_ZOOM = 8;

export function createCamera(x = 0, y = 0, zoom = 1): Camera {
  return { x, y, zoom: clamp(zoom, MIN_ZOOM, MAX_ZOOM) };
}

export function worldToScreen(camera: Camera, viewport: Size, point: Point): Point {
  return {
    x: (point.x - camera.x) * camera.zoom + viewport.width / 2,
    y: (point.y - camera.y) * camera.zoom + viewport.height / 2,
  };
}

export function screenToWorld(camera: Camera, viewport: Size, point: Point): Point {
  return {
    x: (point.x - viewport.width / 2) / camera.zoom + camera.x,
    y: (point.y - viewport.height / 2) / camera.zoom + camera.y,
  };
}

/** Ekran pikseli kadar kaydırır. */
export function panBy(camera: Camera, dx: number, dy: number): Camera {
  return {
    x: camera.x - dx / camera.zoom,
    y: camera.y - dy / camera.zoom,
    zoom: camera.zoom,
  };
}

/** Ekran noktasının altındaki dünya noktasını sabit tutarak yakınlaştırır. */
export function zoomAt(
  camera: Camera,
  viewport: Size,
  screenX: number,
  screenY: number,
  factor: number,
  minZoom = MIN_ZOOM,
  maxZoom = MAX_ZOOM
): Camera {
  const nextZoom = clamp(camera.zoom * factor, minZoom, maxZoom);
  if (nextZoom === camera.zoom) return { ...camera };
  const anchor = screenToWorld(camera, viewport, { x: screenX, y: screenY });
  return {
    x: anchor.x - (screenX - viewport.width / 2) / nextZoom,
    y: anchor.y - (screenY - viewport.height / 2) / nextZoom,
    zoom: nextZoom,
  };
}

/** Sınır kutusunu dolgu ile görüntü alanına sığdırır. */
export function fitToView(
  bbox: BBox | null,
  viewport: Size,
  padding = 56,
  minZoom = MIN_ZOOM,
  maxZoom = MAX_ZOOM
): Camera {
  if (!bbox) return createCamera(0, 0, 1);
  const centerX = (bbox.minX + bbox.maxX) / 2;
  const centerY = (bbox.minY + bbox.maxY) / 2;
  const width = Math.max(bbox.maxX - bbox.minX, 1);
  const height = Math.max(bbox.maxY - bbox.minY, 1);
  const availableWidth = Math.max(viewport.width - padding * 2, 16);
  const availableHeight = Math.max(viewport.height - padding * 2, 16);
  const zoom = clamp(Math.min(availableWidth / width, availableHeight / height), minZoom, maxZoom);
  return { x: centerX, y: centerY, zoom };
}

/** Ekran dikdörtgenini dünya dikdörtgenine çevirir. */
export function screenRectToWorld(
  camera: Camera,
  viewport: Size,
  rect: { x: number; y: number; width: number; height: number }
): BBox {
  const topLeft = screenToWorld(camera, viewport, { x: rect.x, y: rect.y });
  const bottomRight = screenToWorld(camera, viewport, { x: rect.x + rect.width, y: rect.y + rect.height });
  return {
    minX: Math.min(topLeft.x, bottomRight.x),
    minY: Math.min(topLeft.y, bottomRight.y),
    maxX: Math.max(topLeft.x, bottomRight.x),
    maxY: Math.max(topLeft.y, bottomRight.y),
  };
}
