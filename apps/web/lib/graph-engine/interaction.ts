/**
 * Etkileşim katmanı: sürükle-pan, tekerlek-zoom, düğüm sürükleme (fixed),
 * tıklama/çift tıklama/hover, shift-sürükle kutu seçimi, çoklu seçim
 * (ctrl/cmd tık aç-kapat, shift tık ekle), sağ tık bağlam menüsü olayı ve
 * klavye (Tab ile düğüm gezme, Enter seçme, Esc temizleme).
 *
 * DOM'a yalnızca bu dosya bağımlıdır; `destroy()` tüm dinleyicileri kaldırır.
 */

// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { panBy, zoomAt } from "./camera.ts";
// @ts-ignore -- Aynı gerekçe.
import type { Camera, GEdge, GNode, Point, Rect, Size } from "./types.ts";
// @ts-ignore -- Aynı gerekçe.
import { normalizeRect } from "./types.ts";
// @ts-ignore -- Aynı gerekçe.
import type { SelectMode } from "./selection.ts";

export interface InteractionHost {
  canvas: HTMLCanvasElement;
  viewport(): Size;
  camera(): Camera;
  setCamera(camera: Camera): void;
  nodes(): readonly GNode[];
  pickNode(screenX: number, screenY: number): GNode | null;
  pickEdge(screenX: number, screenY: number): GEdge | null;
  nodesInScreenRect(rect: Rect): GNode[];
  setSelectionRect(rect: Rect | null): void;
  /** `mode`: replace / add / toggle. `id: null` seçimi temizler. */
  select(id: string | null, mode: SelectMode): void;
  hover(id: string | null): void;
  hoverEdge(id: string | null, point: Point): void;
  edgeClick(id: string, point: Point): void;
  doubleClick(id: string | null): void;
  boxSelect(ids: string[], additive: boolean): void;
  /** Sağ tık; hedef düğüm veya boş alan. */
  contextMenu(targetId: string | null, point: Point): void;
  /** Tıklama mini haritaya düştüyse kamerayı taşır ve `true` döner. */
  minimapNavigate(point: Point): boolean;
  /** Klavye odağını bir düğüm ileri/geri taşır; çıkılacaksa `false`. */
  focusStep(direction: 1 | -1): boolean;
  /** Enter: odaktaki düğümü seçer. */
  activateFocused(): void;
  nodeMoved(node: GNode): void;
  fit(): void;
  invalidate(): void;
  setCursor(cursor: string): void;
}

type DragMode = "none" | "pan" | "node" | "box";

const CLICK_THRESHOLD = 3;
const KEY_PAN_STEP = 48;
const KEY_PAN_STEP_FAST = 200;
const KEY_ZOOM_FACTOR = 1.2;

export class InteractionController {
  private readonly host: InteractionHost;
  private mode: DragMode = "none";
  private pointerId: number | null = null;
  private startX = 0;
  private startY = 0;
  private lastX = 0;
  private lastY = 0;
  private moved = false;
  private dragNode: GNode | null = null;
  private pressedEdge: GEdge | null = null;
  private clickMode: SelectMode = "replace";
  private boxAdditive = false;
  private hovering: string | null = null;
  private hoveringEdge: string | null = null;
  private destroyed = false;

  constructor(host: InteractionHost) {
    this.host = host;
    const canvas = host.canvas;
    canvas.addEventListener("pointerdown", this.onPointerDown);
    canvas.addEventListener("pointermove", this.onPointerMove);
    canvas.addEventListener("pointerup", this.onPointerUp);
    canvas.addEventListener("pointercancel", this.onPointerUp);
    canvas.addEventListener("pointerleave", this.onPointerLeave);
    canvas.addEventListener("wheel", this.onWheel, { passive: false });
    canvas.addEventListener("dblclick", this.onDoubleClick);
    canvas.addEventListener("contextmenu", this.onContextMenu);
    canvas.addEventListener("keydown", this.onKeyDown);
    canvas.tabIndex = canvas.tabIndex >= 0 ? canvas.tabIndex : 0;
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    const canvas = this.host.canvas;
    canvas.removeEventListener("pointerdown", this.onPointerDown);
    canvas.removeEventListener("pointermove", this.onPointerMove);
    canvas.removeEventListener("pointerup", this.onPointerUp);
    canvas.removeEventListener("pointercancel", this.onPointerUp);
    canvas.removeEventListener("pointerleave", this.onPointerLeave);
    canvas.removeEventListener("wheel", this.onWheel);
    canvas.removeEventListener("dblclick", this.onDoubleClick);
    canvas.removeEventListener("contextmenu", this.onContextMenu);
    canvas.removeEventListener("keydown", this.onKeyDown);
    this.dragNode = null;
  }

  private localPoint(event: MouseEvent | PointerEvent | WheelEvent): Point {
    const rect = this.host.canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  /** Ctrl/Cmd aç-kapat, Shift ekle; ikisi birlikteyse aç-kapat önceliklidir. */
  private selectModeFor(event: PointerEvent): SelectMode {
    if (event.ctrlKey || event.metaKey) return "toggle";
    if (event.shiftKey) return "add";
    return "replace";
  }

  private onPointerDown = (event: PointerEvent): void => {
    if (event.button === 2) {
      // Sağ tık menüsü `contextmenu` olayında açılır; burada sürükleme başlamaz.
      return;
    }
    if (event.button !== 0) return;
    const point = this.localPoint(event);
    // Mini harita tıklaması kamerayı taşır; seçim başlamaz.
    if (this.host.minimapNavigate(point)) {
      event.preventDefault();
      return;
    }
    this.startX = point.x;
    this.startY = point.y;
    this.lastX = point.x;
    this.lastY = point.y;
    this.moved = false;
    this.clickMode = this.selectModeFor(event);
    this.boxAdditive = event.ctrlKey || event.metaKey;
    this.pointerId = event.pointerId;
    this.host.canvas.setPointerCapture?.(event.pointerId);

    const node = this.host.pickNode(point.x, point.y);
    const wantsMulti = event.shiftKey || event.ctrlKey || event.metaKey;
    if (wantsMulti && !node) {
      // Boş alanda Shift/Ctrl-sürükle: kutu seçimi.
      this.mode = "box";
      this.dragNode = null;
      this.pressedEdge = null;
      this.host.setSelectionRect({ x: point.x, y: point.y, width: 0, height: 0 });
    } else if (node) {
      this.mode = "node";
      this.dragNode = node;
      this.pressedEdge = null;
      // Sabitleme yalnız gerçek sürüklemede yapılır; tıklama seçimi düğümü
      // force yerleşimine kilitlememeli.
      node.vx = 0;
      node.vy = 0;
      this.host.setCursor("grabbing");
    } else {
      this.mode = "pan";
      this.dragNode = null;
      this.pressedEdge = this.host.pickEdge(point.x, point.y);
      this.host.setCursor("grabbing");
    }
    this.host.canvas.focus?.();
    event.preventDefault();
  };

  private openContext(point: Point): void {
    const node = this.host.pickNode(point.x, point.y);
    this.host.contextMenu(node ? node.id : null, point);
  }

  private onPointerMove = (event: PointerEvent): void => {
    const point = this.localPoint(event);
    if (this.mode === "none") {
      this.updateHover(point);
      return;
    }

    const totalDx = point.x - this.startX;
    const totalDy = point.y - this.startY;
    if (!this.moved && Math.hypot(totalDx, totalDy) > CLICK_THRESHOLD) this.moved = true;

    if (this.mode === "pan") {
      this.host.setCamera(panBy(this.host.camera(), point.x - this.lastX, point.y - this.lastY));
      this.host.invalidate();
    } else if (this.mode === "node" && this.dragNode) {
      if (!this.moved) return;
      this.dragNode.fixed = true;
      const world = screenToWorldPoint(this.host.camera(), this.host.viewport(), point);
      this.dragNode.x = world.x;
      this.dragNode.y = world.y;
      this.dragNode.vx = 0;
      this.dragNode.vy = 0;
      this.host.nodeMoved(this.dragNode);
      this.host.invalidate();
    } else if (this.mode === "box") {
      this.host.setSelectionRect(normalizeRect(this.startX, this.startY, point.x, point.y));
      this.host.invalidate();
    }

    this.lastX = point.x;
    this.lastY = point.y;
  };

  private onPointerUp = (event: PointerEvent): void => {
    if (this.mode === "none") {
      this.pointerId = null;
      return;
    }
    const point = this.localPoint(event);
    this.host.canvas.releasePointerCapture?.(event.pointerId);

    if (this.mode === "box") {
      this.host.setSelectionRect(null);
      if (this.moved) {
        const rect = normalizeRect(this.startX, this.startY, point.x, point.y);
        const ids = this.host.nodesInScreenRect(rect).map((node) => node.id);
        this.host.boxSelect(ids, this.boxAdditive);
      } else {
        this.host.select(null, "replace");
      }
    } else if (this.mode === "node" && this.dragNode) {
      if (!this.moved) {
        this.host.select(this.dragNode.id, this.clickMode);
      } else {
        this.host.nodeMoved(this.dragNode);
      }
    } else if (this.mode === "pan" && !this.moved) {
      if (this.pressedEdge) this.host.edgeClick(this.pressedEdge.id, point);
      else this.host.select(null, "replace");
    }

    this.mode = "none";
    this.dragNode = null;
    this.pressedEdge = null;
    this.pointerId = null;
    this.host.setCursor("default");
    this.host.invalidate();
  };

  private onPointerLeave = (): void => {
    if (this.mode !== "none") return;
    if (this.hovering !== null) {
      this.hovering = null;
      this.host.hover(null);
    }
    if (this.hoveringEdge !== null) {
      this.hoveringEdge = null;
      this.host.hoverEdge(null, { x: 0, y: 0 });
    }
  };

  private onWheel = (event: WheelEvent): void => {
    event.preventDefault();
    const point = this.localPoint(event);
    const factor = Math.exp(-event.deltaY * 0.0015);
    this.host.setCamera(zoomAt(this.host.camera(), this.host.viewport(), point.x, point.y, factor));
    this.host.invalidate();
  };

  private onDoubleClick = (event: MouseEvent): void => {
    const point = this.localPoint(event);
    const node = this.host.pickNode(point.x, point.y);
    this.host.doubleClick(node ? node.id : null);
  };

  private onContextMenu = (event: MouseEvent): void => {
    event.preventDefault();
    this.openContext(this.localPoint(event));
  };

  private onKeyDown = (event: KeyboardEvent): void => {
    const step = event.shiftKey ? KEY_PAN_STEP_FAST : KEY_PAN_STEP;
    switch (event.key) {
      case "Tab": {
        const retained = this.host.focusStep(event.shiftKey ? -1 : 1);
        // Son düğümden sonra odak canvas'tan çıkar (klavye tuzağı yok).
        if (retained) {
          event.preventDefault();
          this.host.invalidate();
        }
        return;
      }
      case "Enter":
      case " ":
        this.host.activateFocused();
        event.preventDefault();
        this.host.invalidate();
        return;
      case "Escape":
        this.host.select(null, "replace");
        event.preventDefault();
        this.host.invalidate();
        return;
      case "ArrowLeft":
        this.host.setCamera(panBy(this.host.camera(), step, 0));
        break;
      case "ArrowRight":
        this.host.setCamera(panBy(this.host.camera(), -step, 0));
        break;
      case "ArrowUp":
        this.host.setCamera(panBy(this.host.camera(), 0, step));
        break;
      case "ArrowDown":
        this.host.setCamera(panBy(this.host.camera(), 0, -step));
        break;
      case "+":
      case "=":
        this.zoomFromCenter(KEY_ZOOM_FACTOR);
        break;
      case "-":
      case "_":
        this.zoomFromCenter(1 / KEY_ZOOM_FACTOR);
        break;
      case "f":
      case "F":
        this.host.fit();
        break;
      default:
        return;
    }
    event.preventDefault();
    this.host.invalidate();
  };

  private zoomFromCenter(factor: number): void {
    const viewport = this.host.viewport();
    this.host.setCamera(
      zoomAt(this.host.camera(), viewport, viewport.width / 2, viewport.height / 2, factor)
    );
  }

  private updateHover(point: Point): void {
    const node = this.host.pickNode(point.x, point.y);
    const id = node ? node.id : null;
    if (id !== this.hovering) {
      this.hovering = id;
      this.host.hover(id);
    }

    const edge = node ? null : this.host.pickEdge(point.x, point.y);
    const edgeId = edge ? edge.id : null;
    this.host.setCursor(node || edge ? "pointer" : "default");
    if (edgeId === this.hoveringEdge) return;
    this.hoveringEdge = edgeId;
    this.host.hoverEdge(edgeId, point);
    this.host.invalidate();
  }
}

function screenToWorldPoint(camera: Camera, viewport: Size, point: Point): Point {
  return {
    x: (point.x - viewport.width / 2) / camera.zoom + camera.x,
    y: (point.y - viewport.height / 2) / camera.zoom + camera.y,
  };
}
