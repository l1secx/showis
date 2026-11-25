/**
 * Grafik motorunun tip katmanı ve varsayılan teması.
 *
 * Bu dosya DOM'a bağımlı değildir; simülasyon, yerleşim ve dışa aktarım
 * katmanları buradaki tanımları paylaşır.
 */

/** Grafik düğümü. Motor konum ve hız alanlarını doğrudan günceller. */
export interface GNode {
  id: string;
  label: string;
  /** Düğüm türü; şekli belirler. */
  kind: string;
  /** 1 ve üzeri; yarıçap ve kenar kalınlığı için kullanılır. */
  weight: number;
  cluster?: string | null;
  x: number;
  y: number;
  vx: number;
  vy: number;
  /** Kullanıcı taşıdıysa veya yerleşim sabitlediyse gerçek değildir. */
  fixed?: boolean;
  /** Düğüm kontur rengi; verilmezse tema rengi kullanılır. */
  color?: string;
  /** Kesikli çerçeve; onay bekleyen kayıtlar için. */
  dashed?: boolean;
  /** Soluk çizim; reddedilmiş veya ikincil kayıtlar için. */
  faded?: boolean;
  meta?: Record<string, unknown>;
}

/** Grafik kenarı. */
export interface GEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  weight: number;
  directed?: boolean;
  /** Kenar rengi; verilmezse tema rengi kullanılır. */
  color?: string;
  /** Kesikli kenar; onay bekleyen ilişkiler için. */
  dashed?: boolean;
  /** Soluk kenar; reddedilmiş veya ikincil ilişkiler için. */
  faded?: boolean;
  meta?: Record<string, unknown>;
}

/** Motorun kabul ettiği giriş; konum alanları isteğe bağlıdır. */
export type GraphNodeInput = Omit<GNode, "x" | "y" | "vx" | "vy"> &
  Partial<Pick<GNode, "x" | "y" | "vx" | "vy" | "fixed">>;

export type GraphEdgeInput = Omit<GEdge, "directed"> & Partial<Pick<GEdge, "directed">>;

export interface GraphData {
  nodes: GraphNodeInput[];
  edges: GraphEdgeInput[];
}

export interface Point {
  x: number;
  y: number;
}

export interface BBox {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

export interface Size {
  width: number;
  height: number;
}

/** Ekran uzayında dikdörtgen (sol üst köşe + boyut). */
export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Kamera: `x,y` görüntü alanının merkezindeki dünya noktasıdır.
 * Ekran dönüşümü: `screen = (world - camera) * zoom + viewport / 2`.
 */
export interface Camera {
  x: number;
  y: number;
  zoom: number;
}

/** Renk paleti. Zemin saf siyahtır; dolgu gri değildir. */
export interface Theme {
  background: string;
  /** Düğüm konturu (yarı saydam beyaz). */
  nodeStroke: string;
  /** Düğüm dolgusu; kenarları gizlemek için zeminle aynı. */
  nodeFill: string;
  label: string;
  labelMuted: string;
  edge: string;
  edgeMuted: string;
  /** Tek vurgu rengi: seçim, komşu ve küme halesi. */
  accent: string;
  /** Yol vurgusu (en kısa yol). */
  path: string;
  cluster: string;
  selectionBox: string;
  /** Seçim dışındaki öğelerin opaklığı. */
  dimAlpha: number;
  fontFamily: string;
}

export const DARK_THEME: Theme = {
  background: "#000000",
  nodeStroke: "rgba(255,255,255,0.82)",
  nodeFill: "#000000",
  label: "rgba(255,255,255,0.93)",
  labelMuted: "rgba(255,255,255,0.45)",
  edge: "rgba(255,255,255,0.26)",
  edgeMuted: "rgba(255,255,255,0.09)",
  accent: "#5eead4",
  path: "#f0b429",
  cluster: "rgba(94,234,212,0.055)",
  selectionBox: "rgba(94,234,212,0.85)",
  dimAlpha: 0.16,
  fontFamily: "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif",
};

/** Verilen paleti varsayılanlarla birleştirir. */
export function createTheme(overrides: Partial<Theme> = {}): Theme {
  return { ...DARK_THEME, ...overrides };
}

/**
 * Düğüm türüne göre kontur rengi. Tonlar düşük doygunlukta ve siyah zeminde
 * okunur; vurgu rengi yalnız seçim/hover için ayrılmıştır.
 */
export const NODE_KIND_COLORS: Readonly<Record<string, string>> = {
  company: "#9db4c0",
  supplier: "#a8bfa0",
  product: "#c9b28f",
  person: "#b8a6c9",
  location: "#9fb8b3",
  domain: "#8fa8bf",
  other: "#8d96a2",
};

/** Bilinmeyen tür `other` tonuna düşer. */
export function nodeKindColor(kind: string): string {
  const key = kind.trim().toLowerCase();
  return NODE_KIND_COLORS[key] ?? NODE_KIND_COLORS.other;
}

export function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, value));
}

/** Düğüm yarıçapı (dünya birimi); simülasyon ve çizici aynı değeri kullanır. */
export function nodeRadius(node: Pick<GNode, "weight">): number {
  const weight = Number.isFinite(node.weight) ? node.weight : 1;
  return clamp(4 + Math.sqrt(Math.max(0, weight)) * 2.4, 4, 26);
}

/** Ekranda çizilen en küçük düğüm yarıçapı (piksel). */
export const MIN_SCREEN_NODE_RADIUS = 3;

/** Düğüm sayısına göre yoğunluk çarpanı: kalabalık grafikte baz yarıçap küçülür. */
export function nodeDensityScale(nodeCount: number): number {
  if (!Number.isFinite(nodeCount) || nodeCount <= 1) return 1;
  return clamp(Math.sqrt(120 / nodeCount), 0.35, 1);
}

/**
 * Ekran uzayında düğüm yarıçapı (piksel). Yakınlaştıkça büyür, uzaklaşınca
 * küçülür ama `MIN_SCREEN_NODE_RADIUS` altına inmez. Kalabalık grafikte baz
 * yarıçap düşer; 135 düğümde ortalama düğüm ~5-7 px kalır.
 */
export function screenNodeRadius(
  node: Pick<GNode, "weight">,
  zoom: number,
  nodeCount: number
): number {
  const world = nodeRadius(node);
  const density = nodeDensityScale(nodeCount);
  const zoomScale = clamp(Math.sqrt(Math.max(0, zoom) / 0.8), 0.5, 2.4);
  return clamp(world * density * zoomScale * 0.5, MIN_SCREEN_NODE_RADIUS, 26);
}

/** Boş küme için `null` döner. */
export function bboxOfPoints(points: readonly Point[]): BBox | null {
  if (points.length === 0) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const point of points) {
    if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) continue;
    if (point.x < minX) minX = point.x;
    if (point.y < minY) minY = point.y;
    if (point.x > maxX) maxX = point.x;
    if (point.y > maxY) maxY = point.y;
  }
  if (!Number.isFinite(minX) || !Number.isFinite(minY)) return null;
  return { minX, minY, maxX, maxY };
}

/** Düğümlerin (yarıçapları dahil) sınır kutusu. */
export function bboxOfNodes(nodes: readonly Pick<GNode, "x" | "y" | "weight">[]): BBox | null {
  if (nodes.length === 0) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const node of nodes) {
    if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) continue;
    const radius = nodeRadius(node) + 2;
    if (node.x - radius < minX) minX = node.x - radius;
    if (node.y - radius < minY) minY = node.y - radius;
    if (node.x + radius > maxX) maxX = node.x + radius;
    if (node.y + radius > maxY) maxY = node.y + radius;
  }
  if (!Number.isFinite(minX) || !Number.isFinite(minY)) return null;
  return { minX, minY, maxX, maxY };
}

export function rectContainsPoint(rect: Rect, x: number, y: number): boolean {
  return x >= rect.x && x <= rect.x + rect.width && y >= rect.y && y <= rect.y + rect.height;
}

export function normalizeRect(ax: number, ay: number, bx: number, by: number): Rect {
  return {
    x: Math.min(ax, bx),
    y: Math.min(ay, by),
    width: Math.abs(ax - bx),
    height: Math.abs(ay - by),
  };
}
