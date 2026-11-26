/**
 * Canvas2D çizici: devicePixelRatio, kirli-bayrak + requestAnimationFrame,
 * LOD, kavisli çoklu kenarlar, oklar, etiket çakışma kırpma, seçim vurgusu.
 *
 * Çizim geometrisi yardımcıları (`shapePoints`, `edgeGeometry`) SVG dışa
 * aktarımıyla paylaşılır; böylece PNG ve SVG aynı sahneyi temsil eder.
 */

// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { worldToScreen } from "./camera.ts";
// @ts-ignore -- Aynı gerekçe.
import type { Camera, GEdge, GNode, Point, Rect, Size, Theme } from "./types.ts";
// @ts-ignore -- Aynı gerekçe.
import { clamp, createTheme, screenNodeRadius } from "./types.ts";
// @ts-ignore -- Aynı gerekçe.
import { MINIMAP_DEFAULT_SIZE, drawMinimap, minimapOrigin } from "./minimap.ts";

export type NodeShape = "circle" | "square" | "diamond" | "hexagon" | "triangle";

export interface RenderScene {
  nodes: readonly GNode[];
  edges: readonly GEdge[];
  /** Birincil seçim; geriye dönük tek seçim. */
  selectedId?: string | null;
  /** Çoklu seçim; `selectedId` ile birleşir. */
  selectedIds?: readonly string[];
  /** Klavye odağı; çizici odak halkası çizer. */
  focusId?: string | null;
  hoveredId?: string | null;
  hoveredEdgeId?: string | null;
  selectionRect?: Rect | null;
  /** En kısa yol vurgusu: düğüm ve kenar kimlikleri. */
  pathNodeIds?: readonly string[];
  pathEdgeIds?: readonly string[];
}

/** Seçim/yol/hover durumuna göre renk seçimi için ortak durum. */
export interface PaintState {
  selectedId?: string | null;
  selectedIds?: ReadonlySet<string>;
  path?: boolean;
  hovered?: boolean;
}

function inSelection(state: PaintState, id: string): boolean {
  if (state.selectedIds?.has(id)) return true;
  return id === (state.selectedId ?? null);
}

function hasSelection(state: PaintState): boolean {
  return state.selectedId !== null && state.selectedId !== undefined
    ? true
    : (state.selectedIds?.size ?? 0) > 0;
}

/** Kenar rengi: seçim > yol > hover > soluk > kenar rengi. */
export function edgeStroke(
  theme: Theme,
  edge: Pick<GEdge, "source" | "target" | "color">,
  state: PaintState = {}
): string {
  if (inSelection(state, edge.source) || inSelection(state, edge.target)) {
    return theme.accent;
  }
  if (state.path) return theme.path;
  if (state.hovered) return theme.accent;
  if (hasSelection(state)) return theme.edgeMuted;
  return edge.color ?? theme.edge;
}

/** Düğüm kontur rengi: seçim/hover > yol > düğüm rengi. */
export function nodeStroke(
  theme: Theme,
  node: Pick<GNode, "id" | "color">,
  state: PaintState = {}
): string {
  if (inSelection(state, node.id) || state.hovered) return theme.accent;
  if (state.path) return theme.path;
  return node.color ?? theme.nodeStroke;
}

/** Kenar kalınlığı; seçim/yol vurgusu kalınlaştırır, yoğun grafikte incelir. */
export function edgeWidth(
  edge: Pick<GEdge, "weight">,
  options: { dense?: boolean; emphasis?: boolean } = {}
): number {
  const base = 0.55 + clamp(edge.weight, 0, 8) * 0.22;
  const scale = options.dense ? 0.55 : 1;
  return Math.max(0.35, base * scale * (options.emphasis ? 1.6 : 1));
}

export interface GraphRendererOptions {
  theme?: Theme;
  /** Etiketlerin görünmeye başladığı yakınlaştırma. */
  labelMinZoom?: number;
  /** Bu ağırlığın üstündeki düğümler daha uzaktan etiketlenir. */
  labelMinWeight?: number;
  /** Okların görünmeye başladığı yakınlaştırma. */
  arrowMinZoom?: number;
  /** Bu düğüm sayısının üzerinde kenarlar inceltilir. */
  denseNodeThreshold?: number;
  /** Aynı anda çizilecek en fazla etiket. */
  maxLabels?: number;
  /** Kenar etiketlerinin görünmeye başladığı yakınlaştırma. */
  edgeLabelMinZoom?: number;
  /** Aynı anda çizilecek en fazla kenar etiketi. */
  maxEdgeLabels?: number;
  /** Çoklu kenar ayrım aralığı (piksel). */
  edgeCurveSpacing?: number;
  /** Mini harita çizilsin mi; varsayılan `true`. */
  minimap?: boolean;
  /** Mini harita boyutu (CSS piksel). */
  minimapSize?: number;
}

interface ResolvedRendererOptions {
  labelMinZoom: number;
  labelMinWeight: number;
  arrowMinZoom: number;
  denseNodeThreshold: number;
  maxLabels: number;
  edgeLabelMinZoom: number;
  maxEdgeLabels: number;
  edgeCurveSpacing: number;
  minimap: boolean;
  minimapSize: number;
}

const RENDERER_DEFAULTS: ResolvedRendererOptions = {
  labelMinZoom: 0.9,
  labelMinWeight: 28,
  arrowMinZoom: 0.35,
  denseNodeThreshold: 1200,
  maxLabels: 80,
  edgeLabelMinZoom: 1.1,
  maxEdgeLabels: 60,
  edgeCurveSpacing: 18,
  minimap: true,
  minimapSize: MINIMAP_DEFAULT_SIZE.width,
};

/** Düğüm türüne göre şekil. */
export function nodeShapeForKind(kind: string): NodeShape {
  const normalized = kind.toLowerCase();
  if (normalized.includes("platform") || normalized.includes("site")) return "square";
  if (normalized.includes("finding") || normalized.includes("breach")) return "diamond";
  if (normalized.includes("evidence") || normalized.includes("proof")) return "hexagon";
  if (normalized.includes("company") || normalized.includes("organization")) return "hexagon";
  if (normalized.includes("supplier") || normalized.includes("vendor")) return "square";
  if (normalized.includes("product")) return "diamond";
  if (normalized.includes("location")) return "triangle";
  if (normalized.includes("domain")) return "square";
  if (normalized.includes("person")) return "circle";
  return "circle";
}

/** Kesikli çizgi deseni; `dashed` değilse `null` (düz çizgi). */
export function dashPattern(dashed?: boolean): number[] | null {
  return dashed ? [5, 4] : null;
}

/** Soluk öğelerin opaklığı (reddedilmiş/ikincil kayıt). */
export const FADED_ALPHA = 0.4;

/** İki ekran dikdörtgeni kesişiyor mu (etiket çakışma kırpması). */
export function rectsOverlap(a: Rect, b: Rect): boolean {
  return (
    a.x < b.x + b.width &&
    a.x + a.width > b.x &&
    a.y < b.y + b.height &&
    a.y + a.height > b.y
  );
}

/** Kenar etiketi yalnız yakınlaştırılmış LOD'da görünür. */
export function edgeLabelVisible(zoom: number, minZoom: number): boolean {
  return Number.isFinite(zoom) && zoom >= minZoom;
}

export interface LabelVisibilityOptions {
  /** Düğüm ağırlığı; yüksek ağırlıklı düğüm uzaktan etiketlenir. */
  weight?: number;
  /** Uzaktan etiketlenen en düşük ağırlık. */
  minWeight?: number;
  /** Seçili, hover veya seçiliye komşu düğüm her zaman etiketlenir. */
  important?: boolean;
}

/**
 * Düğüm etiketi LOD'u: yeterince yakınlaşınca, yüksek ağırlıklı düğümde
 * veya seçili/hover/komşu düğümde görünür.
 */
export function labelVisible(
  zoom: number,
  minZoom: number,
  options: LabelVisibilityOptions = {}
): boolean {
  if (!Number.isFinite(zoom)) return false;
  if (options.important) return true;
  if (zoom >= minZoom) return true;
  const weight = Number.isFinite(options.weight) ? (options.weight as number) : 0;
  const minWeight = options.minWeight ?? Number.POSITIVE_INFINITY;
  return zoom >= minZoom * 0.55 && weight >= minWeight;
}

/** Rengi verilen opaklıkta `rgba()` biçimine çevirir; bilinmeyen biçim aynen döner. */
export function colorWithAlpha(color: string, alpha: number): string {
  const opacity = clamp(alpha, 0, 1);
  const value = color.trim();
  const shortHex = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(value);
  if (shortHex) {
    const [r, g, b] = shortHex.slice(1).map((part) => parseInt(`${part}${part}`, 16));
    return `rgba(${r},${g},${b},${opacity.toFixed(3)})`;
  }
  const hex = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(value);
  if (hex) {
    const [r, g, b] = hex.slice(1).map((part) => parseInt(part, 16));
    return `rgba(${r},${g},${b},${opacity.toFixed(3)})`;
  }
  const rgb = /^rgba?\(([^)]+)\)$/i.exec(value);
  if (rgb) {
    const parts = rgb[1].split(",").map((part) => part.trim());
    if (parts.length >= 3) return `rgba(${parts[0]},${parts[1]},${parts[2]},${opacity.toFixed(3)})`;
  }
  return value;
}

export type NodeFillEmphasis = "normal" | "selected" | "hovered" | "path";

/**
 * Düğüm dolgusu: tür/platform renginin doygun tonu düşük opaklıkla dolar.
 * Siyah içi boş daire görünümü yerine renkli cam etkisi verir.
 */
export function nodeFill(
  theme: Theme,
  node: Pick<GNode, "color" | "weight">,
  emphasis: NodeFillEmphasis = "normal"
): string {
  const base = node.color ?? theme.nodeStroke;
  const weight = clamp(Number.isFinite(node.weight) ? node.weight : 1, 0, 44);
  const alpha =
    emphasis === "selected"
      ? 0.5
      : emphasis === "hovered"
        ? 0.42
        : emphasis === "path"
          ? 0.38
          : 0.16 + (weight / 44) * 0.14;
  return colorWithAlpha(base, alpha);
}

/** Daire dışındaki şekiller için köşe noktaları; daire `null` döner. */
export function shapePoints(
  shape: NodeShape,
  cx: number,
  cy: number,
  radius: number
): Point[] | null {
  if (shape === "circle") return null;
  if (shape === "square") {
    const half = radius * 0.88;
    return [
      { x: cx - half, y: cy - half },
      { x: cx + half, y: cy - half },
      { x: cx + half, y: cy + half },
      { x: cx - half, y: cy + half },
    ];
  }
  if (shape === "diamond") {
    return [
      { x: cx, y: cy - radius },
      { x: cx + radius, y: cy },
      { x: cx, y: cy + radius },
      { x: cx - radius, y: cy },
    ];
  }
  if (shape === "triangle") {
    return [
      { x: cx, y: cy - radius },
      { x: cx + radius * 0.92, y: cy + radius * 0.78 },
      { x: cx - radius * 0.92, y: cy + radius * 0.78 },
    ];
  }
  const points: Point[] = [];
  for (let index = 0; index < 6; index += 1) {
    const angle = -Math.PI / 2 + (index * Math.PI) / 3;
    points.push({ x: cx + Math.cos(angle) * radius, y: cy + Math.sin(angle) * radius });
  }
  return points;
}

/** Çoklu kenar için kavis kaydırması; tek kenarda 0. */
export function curveOffset(index: number, count: number, spacing = RENDERER_DEFAULTS.edgeCurveSpacing): number {
  if (count <= 1) return 0;
  return (index - (count - 1) / 2) * spacing;
}

export interface EdgeGeometry {
  start: Point;
  control: Point;
  end: Point;
  angle: number;
}

function trimTowards(from: Point, towards: Point, distance: number): Point {
  const dx = towards.x - from.x;
  const dy = towards.y - from.y;
  const length = Math.hypot(dx, dy);
  if (length <= distance || length < 1e-6) return { x: from.x, y: from.y };
  return { x: from.x + (dx / length) * distance, y: from.y + (dy / length) * distance };
}

/** İki ekran noktası arasındaki kavisli kenar geometrisi. */
export function edgeGeometry(
  from: Point,
  to: Point,
  fromRadius: number,
  toRadius: number,
  offset: number
): EdgeGeometry {
  const mx = (from.x + to.x) / 2;
  const my = (from.y + to.y) / 2;
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const length = Math.hypot(dx, dy) || 1;
  const control = {
    x: mx + (-dy / length) * offset,
    y: my + (dx / length) * offset,
  };
  const start = trimTowards(from, control, fromRadius);
  const end = trimTowards(to, control, toRadius);
  return {
    start,
    control,
    end,
    angle: Math.atan2(end.y - control.y, end.x - control.x),
  };
}

export class GraphRenderer {
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;
  private readonly options: ResolvedRendererOptions;
  private theme: Theme;
  private scene: RenderScene = { nodes: [], edges: [] };
  private camera: Camera = { x: 0, y: 0, zoom: 1 };
  private viewport: Size = { width: 0, height: 0 };
  private dpr = 1;
  private raf = 0;
  private dirty = false;
  private destroyed = false;

  constructor(canvas: HTMLCanvasElement, options: GraphRendererOptions = {}) {
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Canvas 2D bağlamı oluşturulamadı.");
    this.canvas = canvas;
    this.ctx = ctx;
    this.theme = options.theme ?? createTheme();
    this.options = {
      labelMinZoom: options.labelMinZoom ?? RENDERER_DEFAULTS.labelMinZoom,
      labelMinWeight: options.labelMinWeight ?? RENDERER_DEFAULTS.labelMinWeight,
      arrowMinZoom: options.arrowMinZoom ?? RENDERER_DEFAULTS.arrowMinZoom,
      denseNodeThreshold: options.denseNodeThreshold ?? RENDERER_DEFAULTS.denseNodeThreshold,
      maxLabels: options.maxLabels ?? RENDERER_DEFAULTS.maxLabels,
      edgeLabelMinZoom: options.edgeLabelMinZoom ?? RENDERER_DEFAULTS.edgeLabelMinZoom,
      maxEdgeLabels: options.maxEdgeLabels ?? RENDERER_DEFAULTS.maxEdgeLabels,
      edgeCurveSpacing: options.edgeCurveSpacing ?? RENDERER_DEFAULTS.edgeCurveSpacing,
      minimap: options.minimap ?? RENDERER_DEFAULTS.minimap,
      minimapSize: options.minimapSize ?? RENDERER_DEFAULTS.minimapSize,
    };
  }

  /** Mini harita açık/kapalı; kapalıyken çizim maliyeti yok. */
  setMinimap(enabled: boolean): void {
    if (this.options.minimap === enabled) return;
    this.options.minimap = enabled;
    this.invalidate();
  }

  get element(): HTMLCanvasElement {
    return this.canvas;
  }

  getTheme(): Theme {
    return this.theme;
  }

  setTheme(theme: Theme): void {
    this.theme = theme;
    this.invalidate();
  }

  setScene(scene: RenderScene): void {
    this.scene = scene;
    this.invalidate();
  }

  setCamera(camera: Camera): void {
    this.camera = camera;
    this.invalidate();
  }

  getCamera(): Camera {
    return this.camera;
  }

  setViewport(width: number, height: number, dpr = 1): void {
    const safeWidth = Math.max(1, Math.round(width));
    const safeHeight = Math.max(1, Math.round(height));
    this.viewport = { width: safeWidth, height: safeHeight };
    this.dpr = Math.max(1, dpr);
    this.canvas.width = Math.round(safeWidth * this.dpr);
    this.canvas.height = Math.round(safeHeight * this.dpr);
    this.canvas.style.width = `${safeWidth}px`;
    this.canvas.style.height = `${safeHeight}px`;
    this.invalidate();
  }

  getViewport(): Size {
    return { ...this.viewport };
  }

  /** Değişiklik varsa bir sonraki karede çiz; yoksa hiç çizme. */
  invalidate(): void {
    if (this.destroyed) return;
    this.dirty = true;
    if (this.raf === 0) {
      this.raf = requestAnimationFrame(this.frame);
    }
  }

  renderNow(): void {
    if (this.destroyed) return;
    if (this.raf !== 0) {
      cancelAnimationFrame(this.raf);
      this.raf = 0;
    }
    this.dirty = false;
    this.draw();
  }

  destroy(): void {
    this.destroyed = true;
    if (this.raf !== 0) {
      cancelAnimationFrame(this.raf);
      this.raf = 0;
    }
    this.scene = { nodes: [], edges: [] };
  }

  private frame = (): void => {
    this.raf = 0;
    if (this.destroyed || !this.dirty) return;
    this.dirty = false;
    this.draw();
  };

  private draw(): void {
    const { ctx, viewport, camera, theme, scene } = this;
    const width = viewport.width;
    const height = viewport.height;
    const zoom = camera.zoom;

    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = theme.background;
    ctx.fillRect(0, 0, width, height);

    const nodes = scene.nodes;
    const edges = scene.edges;
    if (nodes.length === 0) return;

    const screen = new Map<string, Point>();
    const nodeMap = new Map<string, GNode>();
    for (const node of nodes) {
      screen.set(node.id, worldToScreen(camera, viewport, node));
      nodeMap.set(node.id, node);
    }

    const selectedIds = new Set(scene.selectedIds ?? []);
    if (scene.selectedId) selectedIds.add(scene.selectedId);
    const primaryId = scene.selectedId ?? null;
    const focusId = scene.focusId ?? null;
    const hoveredId = scene.hoveredId ?? null;
    const hoveredEdgeId = scene.hoveredEdgeId ?? null;
    const nodeCount = nodes.length;
    const pathNodes = new Set(scene.pathNodeIds ?? []);
    const pathEdges = new Set(scene.pathEdgeIds ?? []);
    const related = new Set<string>();
    for (const id of selectedIds) {
      related.add(id);
      for (const edge of edges) {
        if (edge.source === id) related.add(edge.target);
        else if (edge.target === id) related.add(edge.source);
      }
    }
    const dim = selectedIds.size > 0 && nodes.length > 0;

    if (zoom >= 0.25) this.drawClusterHalos(screen);

    const groups = new Map<string, GEdge[]>();
    for (const edge of edges) {
      const key = edge.source < edge.target ? `${edge.source}\u0000${edge.target}` : `${edge.target}\u0000${edge.source}`;
      const group = groups.get(key);
      if (group) group.push(edge);
      else groups.set(key, [edge]);
    }

    const dense = nodes.length > this.options.denseNodeThreshold;
    const showArrows = zoom >= this.options.arrowMinZoom && nodes.length <= 6000;
    const margin = 80;
    const edgeLabels: { edge: GEdge; point: Point }[] = [];
    const collectEdgeLabels = edgeLabelVisible(zoom, this.options.edgeLabelMinZoom);

    ctx.lineCap = "round";
    for (const group of groups.values()) {
      const groupSize = group.length;
      group.forEach((edge, groupIndex) => {
        const from = screen.get(edge.source);
        const to = screen.get(edge.target);
        if (!from || !to) return;
        if (
          (from.x < -margin && to.x < -margin) ||
          (from.x > width + margin && to.x > width + margin) ||
          (from.y < -margin && to.y < -margin) ||
          (from.y > height + margin && to.y > height + margin)
        ) {
          return;
        }

        const touchesSelection =
          selectedIds.has(edge.source) || selectedIds.has(edge.target);
        const isPath = pathEdges.has(edge.id);
        const isHovered = edge.id === hoveredEdgeId;
        const dimEdge = dim && !touchesSelection;

        ctx.globalAlpha = dimEdge ? theme.dimAlpha : edge.faded ? FADED_ALPHA : 1;
        const stroke = edgeStroke(theme, edge, {
          selectedId: primaryId,
          selectedIds,
          path: isPath,
          hovered: isHovered,
        });
        ctx.strokeStyle = stroke;
        ctx.lineWidth = edgeWidth(edge, {
          dense,
          emphasis: touchesSelection || isPath || isHovered,
        });
        ctx.setLineDash(dashPattern(edge.dashed) ?? []);

        if (edge.source === edge.target) {
          const selfNode = nodeMap.get(edge.source);
          this.drawSelfLoop(
            from,
            selfNode ? screenNodeRadius(selfNode, zoom, nodeCount) : Math.max(3, 5 * zoom)
          );
          ctx.setLineDash([]);
          return;
        }

        const sourceNode = nodeMap.get(edge.source);
        const targetNode = nodeMap.get(edge.target);
        const sourceRadius = sourceNode
          ? screenNodeRadius(sourceNode, zoom, nodeCount)
          : Math.max(3, 5 * zoom);
        const targetRadius = targetNode
          ? screenNodeRadius(targetNode, zoom, nodeCount)
          : Math.max(3, 5 * zoom);
        const offset = curveOffset(groupIndex, groupSize, this.options.edgeCurveSpacing);
        const geometry = edgeGeometry(from, to, sourceRadius, targetRadius, offset);

        ctx.beginPath();
        ctx.moveTo(geometry.start.x, geometry.start.y);
        ctx.quadraticCurveTo(geometry.control.x, geometry.control.y, geometry.end.x, geometry.end.y);
        ctx.stroke();
        ctx.setLineDash([]);

        if (edge.directed && showArrows) {
          this.drawArrow(geometry, Math.max(3.2, 4 + zoom), stroke);
        }

        if (collectEdgeLabels && edge.type) {
          edgeLabels.push({
            edge,
            point: {
              x: (geometry.start.x + 2 * geometry.control.x + geometry.end.x) / 4,
              y: (geometry.start.y + 2 * geometry.control.y + geometry.end.y) / 4,
            },
          });
        }
      });
    }
    ctx.globalAlpha = 1;

    // Düğümler
    const labelCandidates: GNode[] = [];
    for (const node of nodes) {
      const point = screen.get(node.id);
      if (!point) continue;
      const radius = screenNodeRadius(node, zoom, nodeCount);
      if (
        point.x + radius < -margin ||
        point.x - radius > width + margin ||
        point.y + radius < -margin ||
        point.y - radius > height + margin
      ) {
        continue;
      }

      const isSelected = selectedIds.has(node.id);
      const isPrimary = node.id === primaryId;
      const isFocused = node.id === focusId;
      const isHovered = node.id === hoveredId;
      const isRelated = related.has(node.id);
      const dimNode = dim && !isRelated;
      const isPath = pathNodes.has(node.id);

      ctx.globalAlpha = dimNode ? theme.dimAlpha : node.faded ? FADED_ALPHA : 1;
      ctx.fillStyle = nodeFill(
        theme,
        node,
        isSelected ? "selected" : isHovered ? "hovered" : isPath ? "path" : "normal"
      );
      ctx.strokeStyle = nodeStroke(theme, node, {
        selectedId: primaryId,
        selectedIds,
        path: isPath,
        hovered: isHovered,
      });
      ctx.lineWidth = isSelected ? 2.2 : isPath ? 2 : isHovered ? 1.8 : 1.1;
      ctx.setLineDash(dashPattern(node.dashed) ?? []);

      const shape = nodeShapeForKind(node.kind);
      const points = shapePoints(shape, point.x, point.y, radius);
      ctx.beginPath();
      if (!points) {
        ctx.arc(point.x, point.y, radius, 0, Math.PI * 2);
      } else {
        ctx.moveTo(points[0].x, points[0].y);
        for (let index = 1; index < points.length; index += 1) {
          ctx.lineTo(points[index].x, points[index].y);
        }
        ctx.closePath();
      }
      ctx.fill();
      ctx.stroke();
      ctx.setLineDash([]);

      if (isSelected) {
        ctx.globalAlpha = 0.28;
        ctx.beginPath();
        ctx.arc(point.x, point.y, radius + 4 + zoom * 2, 0, Math.PI * 2);
        ctx.strokeStyle = theme.accent;
        ctx.lineWidth = 1;
        ctx.stroke();
        ctx.globalAlpha = 1;
      }

      if (isFocused) {
        ctx.globalAlpha = 1;
        ctx.beginPath();
        ctx.arc(point.x, point.y, radius + 7, 0, Math.PI * 2);
        ctx.strokeStyle = theme.accent;
        ctx.lineWidth = 2;
        ctx.setLineDash([3, 3]);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      if (isSelected || isPrimary || isHovered || isFocused || (dim && isRelated)) {
        labelCandidates.push(node);
      }
    }

    const occupied: Rect[] = [];
    ctx.textBaseline = "middle";
    ctx.font = `12px ${theme.fontFamily}`;
    {
      const ordered = nodes.slice().sort((a, b) => b.weight - a.weight);
      const seen = new Set<string>();
      const queue = [...labelCandidates, ...ordered];
      let drawn = 0;
      const limit = this.options.maxLabels;

      for (const node of queue) {
        if (seen.has(node.id)) continue;
        seen.add(node.id);
        const point = screen.get(node.id);
        if (!point) continue;
        const text = node.label;
        if (!text) continue;
        const isSelected = selectedIds.has(node.id);
        const isHovered = node.id === hoveredId;
        const isFocused = node.id === focusId;
        const important =
          isSelected || isHovered || isFocused || (dim && related.has(node.id));
        if (
          !labelVisible(zoom, this.options.labelMinZoom, {
            weight: node.weight,
            minWeight: this.options.labelMinWeight,
            important,
          })
        ) {
          continue;
        }
        if (!important && drawn >= limit) continue;

        const relatedToSelection = !dim || related.has(node.id);
        const textWidth = ctx.measureText(text).width;
        const radius = screenNodeRadius(node, zoom, nodeCount);
        const x = point.x + radius + 6;
        const y = point.y;
        const rect: Rect = { x, y: y - 8, width: textWidth + 4, height: 16 };
        if (rect.x > width || rect.y > height || rect.y + rect.height < 0) continue;
        if (!important) {
          let overlaps = false;
          for (const existing of occupied) {
            if (rectsOverlap(rect, existing)) {
              overlaps = true;
              break;
            }
          }
          if (overlaps) continue;
        }
        occupied.push(rect);

        ctx.globalAlpha = dim && !relatedToSelection ? theme.dimAlpha : 1;
        ctx.fillStyle = isSelected || isHovered
          ? theme.accent
          : isFocused
            ? theme.label
            : relatedToSelection
              ? theme.label
              : theme.labelMuted;
        ctx.font = `${isSelected || isFocused ? 13 : 12}px ${theme.fontFamily}`;
        ctx.fillText(text, x, y);
        drawn += 1;
      }
      ctx.globalAlpha = 1;
    }

    // Kenar etiketleri: yalnız yakınlaştırılmış LOD'da, düğüm etiketleriyle çakışmadan.
    if (edgeLabels.length > 0) {
      let drawnEdgeLabels = 0;
      ctx.font = `11px ${theme.fontFamily}`;
      for (const candidate of edgeLabels) {
        if (drawnEdgeLabels >= this.options.maxEdgeLabels) break;
        const text = candidate.edge.type;
        if (!text) continue;
        const textWidth = ctx.measureText(text).width;
        const rect: Rect = {
          x: candidate.point.x - textWidth / 2 - 3,
          y: candidate.point.y - 7,
          width: textWidth + 6,
          height: 14,
        };
        if (rect.x + rect.width < 0 || rect.x > width) continue;
        if (rect.y + rect.height < 0 || rect.y > height) continue;
        let overlaps = false;
        for (const existing of occupied) {
          if (rectsOverlap(rect, existing)) {
            overlaps = true;
            break;
          }
        }
        if (overlaps) continue;
        occupied.push(rect);

        const touchesSelection =
          selectedIds.has(candidate.edge.source) || selectedIds.has(candidate.edge.target);
        ctx.globalAlpha = dim && !touchesSelection ? theme.dimAlpha : 1;
        ctx.fillStyle = theme.labelMuted;
        ctx.fillText(text, rect.x + 3, candidate.point.y);
        drawnEdgeLabels += 1;
      }
      ctx.globalAlpha = 1;
    }

    if (scene.selectionRect && scene.selectionRect.width > 1 && scene.selectionRect.height > 1) {
      const rect = scene.selectionRect;
      ctx.globalAlpha = 1;
      ctx.fillStyle = "rgba(94,234,212,0.08)";
      ctx.fillRect(rect.x, rect.y, rect.width, rect.height);
      ctx.strokeStyle = theme.selectionBox;
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = 1;
      ctx.strokeRect(rect.x, rect.y, rect.width, rect.height);
      ctx.setLineDash([]);
    }

    if (this.options.minimap && nodes.length > 1) {
      const size = {
        width: this.options.minimapSize,
        height: Math.round(this.options.minimapSize * 0.66),
      };
      const origin = minimapOrigin(viewport, size, 12);
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      drawMinimap(ctx, {
        points: nodes.map((node) => ({
          x: node.x,
          y: node.y,
          color: node.color ?? theme.labelMuted,
        })),
        camera,
        viewport,
        size,
        theme,
        origin,
      });
    }
  }

  private drawClusterHalos(screen: Map<string, Point>): void {
    const { ctx, theme, camera } = this;
    const clusters = new Map<string, { minX: number; minY: number; maxX: number; maxY: number; count: number }>();
    for (const node of this.scene.nodes) {
      if (!node.cluster) continue;
      const point = screen.get(node.id);
      if (!point) continue;
      const radius = screenNodeRadius(node, camera.zoom, this.scene.nodes.length);
      const existing = clusters.get(node.cluster);
      if (existing) {
        existing.minX = Math.min(existing.minX, point.x - radius);
        existing.minY = Math.min(existing.minY, point.y - radius);
        existing.maxX = Math.max(existing.maxX, point.x + radius);
        existing.maxY = Math.max(existing.maxY, point.y + radius);
        existing.count += 1;
      } else {
        clusters.set(node.cluster, {
          minX: point.x - radius,
          minY: point.y - radius,
          maxX: point.x + radius,
          maxY: point.y + radius,
          count: 1,
        });
      }
    }

    ctx.fillStyle = theme.cluster;
    for (const bounds of clusters.values()) {
      if (bounds.count < 2) continue;
      const cx = (bounds.minX + bounds.maxX) / 2;
      const cy = (bounds.minY + bounds.maxY) / 2;
      const radius = Math.max(bounds.maxX - bounds.minX, bounds.maxY - bounds.minY) / 2 + 24;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  private drawSelfLoop(point: Point, radius: number): void {
    const { ctx } = this;
    const loopRadius = Math.max(6, radius * 0.9);
    ctx.beginPath();
    ctx.arc(point.x + loopRadius, point.y - loopRadius, loopRadius, 0, Math.PI * 2);
    ctx.stroke();
  }

  private drawArrow(geometry: EdgeGeometry, size: number, color: string): void {
    const { ctx } = this;
    const angle = geometry.angle;
    const tip = geometry.end;
    const left = {
      x: tip.x - Math.cos(angle - 0.45) * size,
      y: tip.y - Math.sin(angle - 0.45) * size,
    };
    const right = {
      x: tip.x - Math.cos(angle + 0.45) * size,
      y: tip.y - Math.sin(angle + 0.45) * size,
    };
    ctx.beginPath();
    ctx.moveTo(tip.x, tip.y);
    ctx.lineTo(left.x, left.y);
    ctx.lineTo(right.x, right.y);
    ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
  }
}
