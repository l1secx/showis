/**
 * GraphEngine: veri, tema, yerleşim, seçim ve olay yönetimi.
 *
 * Simülasyon çalışırken tek bir requestAnimationFrame döngüsü tick atar;
 * çizici yalnızca kirlendiğinde çizer. `destroy()` tüm dinleyicileri,
 * gözlemcileri ve döngüleri temizler.
 */

// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { fitToView, screenRectToWorld, screenToWorld } from "./camera.ts";
// @ts-ignore -- Aynı gerekçe.
import type { Camera, GEdge, GNode, GraphData, Point, Rect, Size, Theme } from "./types.ts";
// @ts-ignore -- Aynı gerekçe.
import { bboxOfNodes, createTheme, normalizeRect } from "./types.ts";
// @ts-ignore -- Aynı gerekçe.
import { GraphRenderer, type GraphRendererOptions, type RenderScene } from "./renderer.ts";
// @ts-ignore -- Aynı gerekçe.
import { canvasToPNG, sceneToSVG } from "./export.ts";
// @ts-ignore -- Aynı gerekçe.
import { InteractionController, type InteractionHost } from "./interaction.ts";
// @ts-ignore -- Aynı gerekçe.
import { computeLayout, type LayoutName, type LayoutTargets } from "./layouts.ts";
// @ts-ignore -- Aynı gerekçe.
import { Simulation, type SimulationOptions } from "./simulation.ts";
// @ts-ignore -- Aynı gerekçe.
import { SpatialIndex } from "./spatial-index.ts";
// @ts-ignore -- Aynı gerekçe.
import { minimapTarget } from "./minimap.ts";
// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { applySelection, nextFocusIndex, selectionArray, sortForKeyboard, topConnectedNodes, type ConnectedNode, type SelectMode } from "./selection.ts";

export type GraphLayoutName = LayoutName | "force";

export interface GraphEngineEventMap {
  select: { id: string | null; additive: boolean };
  /** Çoklu seçim her değiştiğinde; `ids` birincil seçim en sonda. */
  selection: { ids: string[]; primary: string | null };
  boxselect: { ids: string[]; additive: boolean };
  hover: { id: string | null };
  /** Kenar hover'ı; `x,y` tuval pikselidir (ipucu konumu). */
  edgehover: { id: string | null; x: number; y: number };
  edgeclick: { id: string; x: number; y: number };
  doubleclick: { id: string | null };
  /** Sağ tık; `id` hedef düğüm, boş alan `null`. */
  contextmenu: { id: string | null; x: number; y: number };
  /** Klavye odağı değişti; `id` odaktaki düğüm. */
  focus: { id: string | null };
  layout: { name: GraphLayoutName; ms: number };
  settle: { iterations: number };
  camera: { camera: Camera };
}

export interface GraphEngineOptions {
  theme?: Theme;
  simulation?: SimulationOptions;
  renderer?: Omit<GraphRendererOptions, "theme">;
  /** Veri yüklendiğinde uygulanacak yerleşim; varsayılan "force". */
  layout?: GraphLayoutName;
  /** fit() kenar boşluğu (piksel). */
  padding?: number;
}

interface Transition {
  starts: Map<string, Point>;
  targets: LayoutTargets;
  startedAt: number;
  duration: number;
  name: GraphLayoutName;
}

const DEFAULT_VIEWPORT: Size = { width: 800, height: 600 };
const LAYOUT_DURATION = 600;

function now(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

function normalizeData(
  data: GraphData,
  previous: Map<string, { x: number; y: number }>
): { nodes: GNode[]; edges: GEdge[] } {
  const nodes: GNode[] = [];
  const seen = new Set<string>();
  for (const input of data.nodes) {
    if (!input || typeof input.id !== "string" || input.id.length === 0) continue;
    if (seen.has(input.id)) continue;
    seen.add(input.id);
    const old = previous.get(input.id);
    const x = Number.isFinite(input.x) ? (input.x as number) : old ? old.x : NaN;
    const y = Number.isFinite(input.y) ? (input.y as number) : old ? old.y : NaN;
    nodes.push({
      id: input.id,
      label: typeof input.label === "string" ? input.label : input.id,
      kind: typeof input.kind === "string" ? input.kind : "entity",
      weight: Number.isFinite(input.weight) ? (input.weight as number) : 1,
      cluster: input.cluster ?? null,
      x,
      y,
      vx: Number.isFinite(input.vx) ? (input.vx as number) : 0,
      vy: Number.isFinite(input.vy) ? (input.vy as number) : 0,
      fixed: input.fixed,
      color: input.color,
      dashed: input.dashed,
      faded: input.faded,
      meta: input.meta,
    });
  }

  const ids = new Set(nodes.map((node) => node.id));
  const edges: GEdge[] = [];
  const edgeIds = new Set<string>();
  data.edges.forEach((input, index) => {
    if (!input || !ids.has(input.source) || !ids.has(input.target)) return;
    const id = typeof input.id === "string" && input.id.length > 0 ? input.id : `${input.source}->${input.target}:${index}`;
    if (edgeIds.has(id)) return;
    edgeIds.add(id);
    edges.push({
      id,
      source: input.source,
      target: input.target,
      type: typeof input.type === "string" ? input.type : "related",
      weight: Number.isFinite(input.weight) ? (input.weight as number) : 1,
      directed: input.directed ?? false,
      color: input.color,
      dashed: input.dashed,
      faded: input.faded,
      meta: input.meta,
    });
  });

  return { nodes, edges };
}

export class GraphEngine {
  private readonly canvas: HTMLCanvasElement;
  private readonly renderer: GraphRenderer;
  private readonly interaction: InteractionController;
  private readonly spatial = new SpatialIndex(64);
  private readonly options: GraphEngineOptions;
  private readonly initialLayout: GraphLayoutName;
  private readonly padding: number;
  private readonly listeners = new Map<keyof GraphEngineEventMap, Set<(payload: never) => void>>();

  private theme: Theme;
  private nodes: GNode[] = [];
  private edges: GEdge[] = [];
  private sim: Simulation | null = null;
  private camera: Camera = { x: 0, y: 0, zoom: 1 };
  private viewport: Size = { ...DEFAULT_VIEWPORT };
  private selectedId: string | null = null;
  /** Çoklu seçim; `selectedId` birincil (en son) seçimdir. */
  private selectedIds = new Set<string>();
  /** Klavye odağı (çizilir, seçim değildir). */
  private focusedId: string | null = null;
  private hoveredId: string | null = null;
  private hoveredEdgeId: string | null = null;
  private pathNodeIds: readonly string[] = [];
  private pathEdgeIds: readonly string[] = [];
  private visibleNodeIds: ReadonlySet<string> | null = null;
  private visibleEdgeIds: ReadonlySet<string> | null = null;
  private selectionRect: Rect | null = null;
  private minimapEnabled = true;
  private transition: Transition | null = null;
  private layoutStartedAt: number | null = null;
  private layoutName: GraphLayoutName | null = null;
  private indexDirty = true;
  private raf = 0;
  private running = false;
  private settleEmitted = true;
  private destroyed = false;
  /** Veri yüklendiğinde veya simülasyon oturunca otomatik sığdırma bekliyor. */
  private pendingFit = true;
  /** Kullanıcı kamerayı elle oynattıysa otomatik sığdırma yapılmaz. */
  private userAdjusted = false;
  private readonly resizeObserver: ResizeObserver | null;

  constructor(container: HTMLElement, options: GraphEngineOptions = {}) {
    this.options = options;
    this.initialLayout = options.layout ?? "force";
    this.padding = options.padding ?? 64;
    this.theme = options.theme ?? createTheme();

    this.canvas = document.createElement("canvas");
    this.canvas.style.display = "block";
    this.canvas.style.outline = "none";
    this.canvas.style.touchAction = "none";
    this.canvas.setAttribute("role", "img");
    this.canvas.setAttribute("aria-label", "Varlık grafiği");
    this.canvas.setAttribute(
      "aria-describedby",
      "graph-canvas-help graph-canvas-summary"
    );
    container.appendChild(this.canvas);

    this.renderer = new GraphRenderer(this.canvas, {
      ...options.renderer,
      theme: this.theme,
    });

    this.measure();
    this.renderer.setViewport(this.viewport.width, this.viewport.height, this.devicePixelRatio());

    this.interaction = new InteractionController(this.createHost());

    this.resizeObserver =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => {
            this.measure();
            this.renderer.setViewport(this.viewport.width, this.viewport.height, this.devicePixelRatio());
            // Kap gerçek boyutunu aldığında (ve kullanıcı kamerayı oynatmadıysa) sığdır.
            if (this.nodes.length > 0 && !this.userAdjusted) this.fit();
            else this.invalidate();
          })
        : null;
    this.resizeObserver?.observe(container);
  }

  // ---------------------------------------------------------------- veri

  setData(data: GraphData, options: { preserveView?: boolean } = {}): void {
    if (this.destroyed) return;
    const previous = new Map<string, { x: number; y: number }>();
    for (const node of this.nodes) previous.set(node.id, { x: node.x, y: node.y });
    const preserveView = options.preserveView === true && this.nodes.length > 0;

    const normalized = normalizeData(data, previous);
    this.nodes = normalized.nodes;
    this.edges = normalized.edges;
    this.sim = new Simulation(this.nodes, this.edges, this.options.simulation);
    this.indexDirty = true;
    this.selectionRect = null;
    this.hoveredId = null;
    this.hoveredEdgeId = null;
    this.visibleNodeIds = null;
    this.visibleEdgeIds = null;
    this.settleEmitted = false;
    const visible = new Set(this.nodes.map((node) => node.id));
    this.selectedIds = new Set([...this.selectedIds].filter((id) => visible.has(id)));
    if (this.selectedId && !visible.has(this.selectedId)) {
      this.selectedId = selectionArray(this.selectedIds, null).at(-1) ?? null;
    }
    if (this.focusedId && !visible.has(this.focusedId)) this.focusedId = null;
    this.transition = null;
    this.layoutStartedAt = now();
    this.layoutName = this.initialLayout;

    if (!preserveView) {
      this.pendingFit = true;
      this.userAdjusted = false;
      this.fit();
      this.renderer.setCamera(this.camera);
    } else {
      this.pendingFit = false;
      this.renderer.setCamera(this.camera);
      this.refreshScene();
    }

    if (this.initialLayout !== "force" && !preserveView) this.layout(this.initialLayout);
    else this.startLoop();
  }

  /**
   * Görünürlük süzgeci. `null` tümünü görünür yapar; kenarlar, iki ucu da
   * görünür ve kimlik kümesinde ise çizilir. Yerleşim ve konumlar korunur.
   */
  setVisibility(
    nodeIds: ReadonlySet<string> | null = null,
    edgeIds: ReadonlySet<string> | null = null
  ): void {
    if (this.destroyed) return;
    this.visibleNodeIds = nodeIds;
    this.visibleEdgeIds = edgeIds;
    this.indexDirty = true;
    if (nodeIds) {
      this.selectedIds = new Set([...this.selectedIds].filter((id) => nodeIds.has(id)));
      if (this.selectedId && !nodeIds.has(this.selectedId)) {
        this.selectedId = selectionArray(this.selectedIds, null).at(-1) ?? null;
      }
      if (this.focusedId && !nodeIds.has(this.focusedId)) this.focusedId = null;
    }
    this.refreshScene();
  }

  /** Mini harita görünürlüğü; çizici sahnesine taşınır. */
  setMinimap(enabled: boolean): void {
    this.minimapEnabled = enabled;
    this.refreshScene();
  }

  /** Vurgulanan kenarı ayarlar (hover). */
  setHoveredEdge(id: string | null): void {
    if (this.hoveredEdgeId === id) return;
    this.hoveredEdgeId = id;
    this.refreshScene();
  }

  /** Yol vurgusu; boş dizi vurguyu kaldırır. */
  setPath(nodeIds: readonly string[] = [], edgeIds: readonly string[] = []): void {
    this.pathNodeIds = nodeIds;
    this.pathEdgeIds = edgeIds;
    this.refreshScene();
  }

  /** Düğüm kimliğine göre kontur rengi; verilmeyen düğüm tema rengine döner. */
  setNodeColors(colors: Readonly<Record<string, string>>): void {
    if (this.destroyed) return;
    for (const node of this.nodes) {
      const color = colors[node.id];
      if (color) node.color = color;
      else delete node.color;
    }
    this.refreshScene();
  }

  /** Kenar türüne göre renk; verilmeyen tür tema rengine döner. */
  setEdgeColors(colors: Readonly<Record<string, string>>): void {
    if (this.destroyed) return;
    for (const edge of this.edges) {
      const color = colors[edge.type];
      if (color) edge.color = color;
      else delete edge.color;
    }
    this.refreshScene();
  }

  setTheme(theme: Theme): void {
    this.theme = theme;
    this.renderer.setTheme(theme);
    this.refreshScene();
  }

  /** İsimli yerleşim hedeflerine animasyonlu geçiş; "force" simülasyonu ısıtır. */
  layout(name: GraphLayoutName): void {
    if (this.destroyed || this.nodes.length === 0) return;
    this.layoutStartedAt = now();
    this.layoutName = name;
    if (name === "force") {
      this.transition = null;
      this.sim?.reheat(1);
      this.settleEmitted = false;
      this.startLoop();
      return;
    }

    const targets = computeLayout(name, this.nodes, this.edges);
    const starts = new Map<string, Point>();
    for (const node of this.nodes) starts.set(node.id, { x: node.x, y: node.y });
    this.transition = {
      starts,
      targets,
      startedAt: now(),
      duration: LAYOUT_DURATION,
      name,
    };
    this.startLoop();
  }

  // ------------------------------------------------------------- seçim

  /**
   * Programatik seçim. `ids` verilirse çoklu seçim kurulur; dizi sırası
   * korunur ve son öğe birincil olur. `select` olayı yaymaz (döngüyü önler).
   */
  select(id: string | null, additional?: readonly string[]): void {
    const visible = new Set(this.visibleNodes().map((node) => node.id));
    const requested = additional ? [...additional] : [];
    if (id !== null) {
      // Tek seçim geriye uyumludur; ikinci argüman yoksa küme tek elemana iner.
      if (!additional) requested.length = 0;
      requested.push(id);
    }
    const next = new Set(requested.filter((item) => visible.has(item)));
    if (next.size === 0 && id !== null) {
      this.selectedIds = new Set();
      this.selectedId = null;
    } else {
      this.selectedIds = next;
      this.selectedId = id !== null && next.has(id) ? id : selectionArray(next, null).at(-1) ?? null;
    }
    this.refreshScene();
  }

  /** Çoklu seçimi doğrudan kurar; birincil kimlik `primary` olur. */
  setSelection(ids: readonly string[], primary: string | null = null): void {
    const visible = new Set(this.visibleNodes().map((node) => node.id));
    const next = new Set(ids.filter((item) => visible.has(item)));
    this.selectedIds = next;
    const resolved =
      primary !== null && next.has(primary)
        ? primary
        : selectionArray(next, null).at(-1) ?? null;
    this.selectedId = resolved;
    this.refreshScene();
  }

  getSelectedId(): string | null {
    return this.selectedId;
  }

  getSelectedIds(): string[] {
    return selectionArray(this.selectedIds, this.selectedId);
  }

  getFocusedId(): string | null {
    return this.focusedId;
  }

  /** Düğüm konumları; daraltma/açma sırasında konum korumak için kullanılır. */
  getPositions(): Record<string, { x: number; y: number }> {
    const positions: Record<string, { x: number; y: number }> = {};
    for (const node of this.nodes) {
      if (Number.isFinite(node.x) && Number.isFinite(node.y)) {
        positions[node.id] = { x: node.x, y: node.y };
      }
    }
    return positions;
  }

  /**
   * Erişilebilir özet: en çok bağlantılı `limit` düğüm. React sarmalayıcısı
   * bunu sr-only listeye basar.
   */
  accessibilitySummary(limit = 10): ConnectedNode[] {
    return topConnectedNodes(this.visibleNodes(), this.visibleEdges(), limit);
  }

  /** Klavye odağını düğüme taşır; yoksa `false`. */
  focusNode(id: string | null): boolean {
    if (id === null) {
      this.focusedId = null;
      this.refreshScene();
      this.emit("focus", { id: null });
      return true;
    }
    const exists = this.visibleNodes().some((node) => node.id === id);
    if (!exists) return false;
    this.focusedId = id;
    this.refreshScene();
    this.emit("focus", { id });
    return true;
  }

  // --------------------------------------------------------- kamera

  fit(): void {
    const bbox = bboxOfNodes(this.visibleNodes());
    this.camera = fitToView(bbox, this.viewport, this.padding);
    this.renderer.setCamera(this.camera);
    this.refreshScene();
    this.emit("camera", { camera: { ...this.camera } });
  }

  /** Kamerayı düğüme ortalar; yakınlaştırma `minZoom` altına inmez. */
  focus(id: string, minZoom = 1.2): void {
    const node = this.visibleNodes().find((item) => item.id === id);
    if (!node) return;
    this.camera = {
      x: node.x,
      y: node.y,
      zoom: Math.max(this.camera.zoom, minZoom),
    };
    this.renderer.setCamera(this.camera);
    this.refreshScene();
    this.emit("camera", { camera: { ...this.camera } });
  }

  /** Verilen düğümleri kapsayan sınır kutusuna sığdırır; boş küme yok sayılır. */
  fitNodes(ids: readonly string[]): void {
    const wanted = new Set(ids);
    const nodes = this.visibleNodes().filter((node) => wanted.has(node.id));
    if (nodes.length === 0) return;
    const bbox = bboxOfNodes(nodes);
    this.userAdjusted = true;
    this.camera = fitToView(bbox, this.viewport, Math.min(this.padding, 48));
    this.renderer.setCamera(this.camera);
    this.refreshScene();
    this.emit("camera", { camera: { ...this.camera } });
  }

  getCamera(): Camera {
    return { ...this.camera };
  }

  getViewport(): Size {
    return { ...this.viewport };
  }

  // -------------------------------------------------------- dışa aktarım

  exportPNG(): Promise<Blob> {
    this.renderer.renderNow();
    return canvasToPNG(this.canvas);
  }

  exportSVG(): string {
    return sceneToSVG(this.scene(), this.camera, this.viewport, this.theme, { padding: 24 });
  }

  // ----------------------------------------------------------- olaylar

  on<K extends keyof GraphEngineEventMap>(
    event: K,
    handler: (payload: GraphEngineEventMap[K]) => void
  ): () => void {
    let handlers = this.listeners.get(event);
    if (!handlers) {
      handlers = new Set();
      this.listeners.set(event, handlers);
    }
    const stored = handler as (payload: never) => void;
    handlers.add(stored);
    return () => {
      this.listeners.get(event)?.delete(stored);
    };
  }

  // ------------------------------------------------------------ yaşam döngüsü

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    if (this.raf !== 0) {
      cancelAnimationFrame(this.raf);
      this.raf = 0;
    }
    this.running = false;
    this.resizeObserver?.disconnect();
    this.interaction.destroy();
    this.renderer.destroy();
    this.canvas.remove();
    this.listeners.clear();
    this.nodes = [];
    this.edges = [];
    this.sim = null;
    this.transition = null;
  }

  // ------------------------------------------------------------- iç işleyiş

  private devicePixelRatio(): number {
    return typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
  }

  private measure(): void {
    const rect = this.canvas.parentElement?.getBoundingClientRect();
    const width = rect?.width ?? 0;
    const height = rect?.height ?? 0;
    this.viewport = {
      width: Math.max(1, Math.round(width || DEFAULT_VIEWPORT.width)),
      height: Math.max(1, Math.round(height || DEFAULT_VIEWPORT.height)),
    };
  }

  private createHost(): InteractionHost {
    return {
      canvas: this.canvas,
      viewport: () => this.getViewport(),
      camera: () => this.camera,
      setCamera: (camera) => {
        this.userAdjusted = true;
        this.camera = camera;
        this.renderer.setCamera(camera);
        this.invalidate();
        this.emit("camera", { camera: { ...camera } });
      },
      nodes: () => this.nodes,
      pickNode: (screenX, screenY) => {
        const world = screenToWorld(this.camera, this.viewport, { x: screenX, y: screenY });
        const tolerance = 4 / this.camera.zoom;
        return this.ensureIndex().hitNode(world.x, world.y, tolerance);
      },
      pickEdge: (screenX, screenY) => {
        const world = screenToWorld(this.camera, this.viewport, { x: screenX, y: screenY });
        return this.ensureIndex().hitEdge(world.x, world.y, 6 / this.camera.zoom);
      },
      nodesInScreenRect: (rect) => {
        const normalized = normalizeRect(rect.x, rect.y, rect.x + rect.width, rect.y + rect.height);
        const worldRect = screenRectToWorld(this.camera, this.viewport, normalized);
        return this.ensureIndex().nodesInRect(
          normalizeRect(worldRect.minX, worldRect.minY, worldRect.maxX, worldRect.maxY)
        );
      },
      setSelectionRect: (rect) => {
        this.selectionRect = rect;
        this.refreshScene();
      },
      select: (id, mode) => {
        this.applyInteractiveSelection(id, mode);
      },
      hover: (id) => {
        this.hoveredId = id;
        this.refreshScene();
        this.emit("hover", { id });
      },
      hoverEdge: (id, point) => {
        this.hoveredEdgeId = id;
        this.refreshScene();
        this.emit("edgehover", { id, x: point.x, y: point.y });
      },
      edgeClick: (id, point) => {
        this.emit("edgeclick", { id, x: point.x, y: point.y });
      },
      doubleClick: (id) => {
        this.emit("doubleclick", { id });
      },
      boxSelect: (ids, additive) => {
        this.emit("boxselect", { ids, additive });
        const next = new Set(ids.filter((id) => this.visibleNodes().some((node) => node.id === id)));
        if (additive) {
          for (const id of this.selectedIds) next.add(id);
        }
        this.selectedIds = next;
        this.selectedId = selectionArray(next, null).at(-1) ?? null;
        this.refreshScene();
        this.emitSelection();
      },
      contextMenu: (id, point) => {
        this.emit("contextmenu", { id, x: point.x, y: point.y });
      },
      minimapNavigate: (point) => {
        if (!this.minimapEnabled) return false;
        const target = minimapTarget(
          point,
          this.visibleNodes(),
          this.camera,
          this.viewport
        );
        if (!target) return false;
        this.userAdjusted = true;
        this.camera = { x: target.x, y: target.y, zoom: this.camera.zoom };
        this.renderer.setCamera(this.camera);
        this.invalidate();
        this.emit("camera", { camera: { ...this.camera } });
        return true;
      },
      focusStep: (direction) => {
        const ordered = sortForKeyboard(this.visibleNodes());
        if (ordered.length === 0) return false;
        const current = ordered.findIndex((node) => node.id === this.focusedId);
        const next = nextFocusIndex(current, direction, ordered.length);
        if (next === null) {
          // Odak canvas'ın dışına çıkar: klavye tuzağı yok, temizle ve bırak.
          this.focusedId = null;
          this.refreshScene();
          this.emit("focus", { id: null });
          return false;
        }
        this.focusedId = ordered[next].id;
        this.refreshScene();
        this.emit("focus", { id: this.focusedId });
        return true;
      },
      activateFocused: () => {
        const id = this.focusedId;
        if (!id) return;
        this.applyInteractiveSelection(id, "replace");
      },
      nodeMoved: () => {
        this.indexDirty = true;
        if (this.sim && this.sim.alpha > 0) this.startLoop();
      },
      fit: () => this.fit(),
      invalidate: () => this.invalidate(),
      setCursor: (cursor) => {
        this.canvas.style.cursor = cursor;
      },
    };
  }

  /** Tıklama seçimini uygular; olayları yayar ve birincil seçimi günceller. */
  private applyInteractiveSelection(id: string | null, mode: SelectMode): void {
    const next = applySelection(this.selectedIds, id, mode);
    const visible = new Set(this.visibleNodes().map((node) => node.id));
    const filtered = new Set([...next].filter((item) => visible.has(item)));
    this.selectedIds = filtered;
    if (id !== null && filtered.has(id)) {
      this.selectedId = id;
    } else {
      this.selectedId = selectionArray(filtered, null).at(-1) ?? null;
    }
    this.refreshScene();
    this.emit("select", { id: this.selectedId, additive: mode !== "replace" });
    this.emitSelection();
  }

  private emitSelection(): void {
    this.emit("selection", {
      ids: selectionArray(this.selectedIds, this.selectedId),
      primary: this.selectedId,
    });
  }

  private visibleNodes(): GNode[] {
    const ids = this.visibleNodeIds;
    if (!ids) return this.nodes;
    return this.nodes.filter((node) => ids.has(node.id));
  }

  private visibleEdges(): GEdge[] {
    const ids = this.visibleEdgeIds;
    const nodeIds = this.visibleNodeIds;
    if (!ids && !nodeIds) return this.edges;
    return this.edges.filter(
      (edge) =>
        (!ids || ids.has(edge.id)) &&
        (!nodeIds || (nodeIds.has(edge.source) && nodeIds.has(edge.target)))
    );
  }

  private ensureIndex(): SpatialIndex {
    if (this.indexDirty) {
      this.spatial.rebuild(this.visibleNodes(), this.visibleEdges());
      this.indexDirty = false;
    }
    return this.spatial;
  }

  private scene(): RenderScene {
    const nodes = this.visibleNodes();
    const edges = this.visibleEdges();
    const exists = (id: string | null) =>
      id !== null && nodes.some((node) => node.id === id);
    return {
      nodes,
      edges,
      selectedId: exists(this.selectedId) ? this.selectedId : null,
      selectedIds: [...this.selectedIds].filter((id) => exists(id)),
      focusId: exists(this.focusedId) ? this.focusedId : null,
      hoveredId: exists(this.hoveredId) ? this.hoveredId : null,
      hoveredEdgeId: this.hoveredEdgeId,
      selectionRect: this.selectionRect,
      pathNodeIds: this.pathNodeIds,
      pathEdgeIds: this.pathEdgeIds,
    };
  }

  private refreshScene(): void {
    this.renderer.setCamera(this.camera);
    this.renderer.setScene(this.scene());
  }

  private invalidate(): void {
    this.renderer.setCamera(this.camera);
    this.renderer.invalidate();
  }

  private emit<K extends keyof GraphEngineEventMap>(event: K, payload: GraphEngineEventMap[K]): void {
    const handlers = this.listeners.get(event);
    if (!handlers) return;
    for (const handler of handlers) {
      (handler as (value: GraphEngineEventMap[K]) => void)(payload);
    }
  }

  private startLoop(): void {
    if (this.running || this.destroyed) return;
    this.running = true;
    this.raf = requestAnimationFrame(this.frame);
  }

  private frame = (timestamp: number): void => {
    this.raf = 0;
    if (this.destroyed) {
      this.running = false;
      return;
    }

    let active = false;
    if (this.transition) {
      active = this.stepTransition(timestamp);
    }
    if (this.transition === null && this.sim && !this.sim.isSettled()) {
      this.sim.tick(1);
      this.indexDirty = true;
      active = true;
    }

    this.refreshScene();
    this.renderer.invalidate();

    if (this.transition === null && this.sim && this.sim.isSettled() && !this.settleEmitted) {
      this.settleEmitted = true;
      this.applyPendingFit();
      this.emit("settle", { iterations: this.sim.iterations });
      this.emitLayoutDone(now());
    }

    if (active) {
      this.raf = requestAnimationFrame(this.frame);
    } else {
      this.running = false;
    }
  };

  private stepTransition(timestamp: number): boolean {
    const transition = this.transition;
    if (!transition) return false;
    const elapsed = timestamp - transition.startedAt;
    const progress = Math.min(1, Math.max(0, elapsed / transition.duration));
    const eased = 1 - Math.pow(1 - progress, 3);

    for (const node of this.nodes) {
      const start = transition.starts.get(node.id);
      const target = transition.targets.get(node.id);
      if (!start || !target) continue;
      node.x = start.x + (target.x - start.x) * eased;
      node.y = start.y + (target.y - start.y) * eased;
      node.vx = 0;
      node.vy = 0;
    }

    if (progress >= 1) {
      for (const node of this.nodes) {
        const target = transition.targets.get(node.id);
        if (!target) continue;
        node.x = target.x;
        node.y = target.y;
      }
      this.transition = null;
      this.sim?.freeze();
      this.sim?.resetVelocities();
      this.indexDirty = true;
      this.applyPendingFit();
      this.emitLayoutDone(timestamp);
      return false;
    }
    return true;
  }

  /** Bekleyen ilk sığdırmayı uygular; kullanıcı kamerayı oynattıysa atlar. */
  private applyPendingFit(): void {
    if (!this.pendingFit) return;
    this.pendingFit = false;
    if (!this.userAdjusted) this.fit();
  }

  /** Sona eren yerleşimin adını ve süresini bildirir. */
  private emitLayoutDone(finishedAt: number): void {
    if (this.layoutStartedAt === null || this.layoutName === null) return;
    const name = this.layoutName;
    const ms = Math.max(0, Math.round(finishedAt - this.layoutStartedAt));
    this.layoutStartedAt = null;
    this.layoutName = null;
    this.emit("layout", { name, ms });
  }
}
