"use client";

/**
 * GraphEngine için ince React sarmalayıcısı. Motor yalnızca tarayıcıda,
 * `useEffect` içinde kurulur; props değişimleri imperatif API'ye aktarılır.
 *
 * Erişilebilirlik: canvas `role="img"` + özet `aria-label` taşır; ek olarak
 * `sr-only` bir liste en çok bağlantılı düğümleri verir. Klavye: Tab düğümler
 * arasında gezer, Enter seçer, Esc seçimi temizler.
 */

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { GraphEngine, type GraphLayoutName } from "./graph-engine";
import type { Camera, GraphData, Theme } from "./types";
import { createTheme } from "./types";
import type { ConnectedNode } from "./selection";

export interface GraphCanvasHandle {
  fit(): void;
  focus(id: string, minZoom?: number): void;
  /** Yerleşimi yeniden uygular (aynı yerleşimde de çalışır). */
  runLayout(name: GraphLayoutName): void;
  /** Verilen düğümleri kapsayan alana sığdırır. */
  fitSelection(ids: readonly string[]): void;
  exportPNG(): Promise<Blob>;
  exportSVG(): string;
  getCamera(): Camera;
  /** Çoklu seçimi kurar; birincil kimlik `primary`. */
  setSelection(ids: readonly string[], primary?: string | null): void;
  getSelection(): string[];
  /** Klavye odağını düğüme taşır. */
  focusNode(id: string | null): void;
  /** Görünüm merkezine göre yakınlaştırır (mobil araç çubuğu). */
  zoomBy(factor: number): void;
  /** Düğüm konumları; açma/daraltma sırasında korunur. */
  getPositions(): Record<string, { x: number; y: number }>;
}

export interface GraphEdgeHover {
  id: string | null;
  /** Tuval pikseli. */
  x: number;
  y: number;
}

/** Sağ tık hedefi; `id` boş alanda `null`. */
export interface GraphContextTarget {
  id: string | null;
  /** Tuval pikseli. */
  x: number;
  y: number;
}

export interface GraphCanvasProps {
  data: GraphData;
  /** Kısmi tema; verilmeyen alanlar `DARK_THEME` ile doldurulur. */
  theme?: Partial<Theme>;
  selectedId?: string | null;
  /** Çoklu seçim; verilirse `selectedId` yerine bu küme çizilir. */
  selectedIds?: readonly string[];
  layout?: GraphLayoutName;
  /** Yalnız bu düğümler çizilir; `null`/tanımsız tümü. */
  visibleNodeIds?: ReadonlySet<string> | null;
  /** Yalnız bu kenarlar çizilir; `null`/tanımsız tümü. */
  visibleEdgeIds?: ReadonlySet<string> | null;
  nodeColors?: Readonly<Record<string, string>>;
  /** Kenar türüne göre renk. */
  edgeColors?: Readonly<Record<string, string>>;
  pathNodeIds?: readonly string[];
  pathEdgeIds?: readonly string[];
  onSelect?: (id: string | null) => void;
  /** Çoklu seçim her değiştiğinde; `ids` birincil en sonda. */
  onSelectionChange?: (ids: string[], primary: string | null) => void;
  onHover?: (id: string | null) => void;
  onEdgeHover?: (event: GraphEdgeHover) => void;
  onEdgeClick?: (event: GraphEdgeHover) => void;
  /** Sağ tık; React tarafı menüyü konumlandırır. */
  onContextMenu?: (event: GraphContextTarget) => void;
  onLayout?: (info: { name: GraphLayoutName; ms: number }) => void;
  /** Mini harita açık mı; varsayılan `true`. */
  minimap?: boolean;
  className?: string;
  style?: CSSProperties;
}

export const GraphCanvas = forwardRef<GraphCanvasHandle, GraphCanvasProps>(function GraphCanvas(
  {
    data,
    theme,
    selectedId,
    selectedIds,
    layout,
    visibleNodeIds,
    visibleEdgeIds,
    nodeColors,
    edgeColors,
    pathNodeIds,
    pathEdgeIds,
    onSelect,
    onSelectionChange,
    onHover,
    onEdgeHover,
    onEdgeClick,
    onContextMenu,
    onLayout,
    minimap = true,
    className,
    style,
  },
  ref
) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const engineRef = useRef<GraphEngine | null>(null);
  const onSelectRef = useRef(onSelect);
  const onSelectionChangeRef = useRef(onSelectionChange);
  const onHoverRef = useRef(onHover);
  const onEdgeHoverRef = useRef(onEdgeHover);
  const onEdgeClickRef = useRef(onEdgeClick);
  const onContextMenuRef = useRef(onContextMenu);
  const onLayoutRef = useRef(onLayout);
  const appliedLayoutRef = useRef<GraphLayoutName | null>(null);
  /** İlk veri yüklemesinden sonra kamera korunur; ilk yüklemede sığdırılır. */
  const preserveViewRef = useRef(false);
  const [summary, setSummary] = useState<ConnectedNode[]>([]);
  const [summaryDirty, setSummaryDirty] = useState(0);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  onSelectRef.current = onSelect;
  onSelectionChangeRef.current = onSelectionChange;
  onHoverRef.current = onHover;
  onEdgeHoverRef.current = onEdgeHover;
  onEdgeClickRef.current = onEdgeClick;
  onContextMenuRef.current = onContextMenu;
  onLayoutRef.current = onLayout;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const engine = new GraphEngine(container);
    engineRef.current = engine;
    engine.setMinimap(minimap);
    const unsubscribers = [
      engine.on("select", (event) => onSelectRef.current?.(event.id)),
      engine.on("selection", (event) => {
        onSelectionChangeRef.current?.(event.ids, event.primary);
        setSummaryDirty((value) => value + 1);
      }),
      engine.on("hover", (event) => onHoverRef.current?.(event.id)),
      engine.on("edgehover", (event) =>
        onEdgeHoverRef.current?.({ id: event.id, x: event.x, y: event.y })
      ),
      engine.on("edgeclick", (event) =>
        onEdgeClickRef.current?.({ id: event.id, x: event.x, y: event.y })
      ),
      engine.on("contextmenu", (event) =>
        onContextMenuRef.current?.({ id: event.id, x: event.x, y: event.y })
      ),
      engine.on("focus", (event) => setFocusedId(event.id)),
      engine.on("layout", (event) => {
        onLayoutRef.current?.({ name: event.name, ms: event.ms });
        setSummaryDirty((value) => value + 1);
      }),
      engine.on("settle", () => setSummaryDirty((value) => value + 1)),
    ];
    return () => {
      for (const unsubscribe of unsubscribers) unsubscribe();
      engine.destroy();
      engineRef.current = null;
    };
    // Mini harita yalnızca ilk kurulumda ayarlanır; sonrası kendi effect'inde.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const engine = engineRef.current;
    if (!engine) return;
    engine.setData(data, {
      preserveView: preserveViewRef.current,
    });
    preserveViewRef.current = true;
    const desired = layout ?? "force";
    if (desired !== "force") engine.layout(desired);
    appliedLayoutRef.current = desired;
    // Yerleşim değişimi ayrı effect'te ele alınır; veri değişiminde son yerleşim korunur.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  useEffect(() => {
    engineRef.current?.setVisibility(visibleNodeIds ?? null, visibleEdgeIds ?? null);
  }, [data, visibleNodeIds, visibleEdgeIds]);

  useEffect(() => {
    if (nodeColors) engineRef.current?.setNodeColors(nodeColors);
  }, [data, nodeColors]);

  useEffect(() => {
    if (edgeColors) engineRef.current?.setEdgeColors(edgeColors);
  }, [data, edgeColors]);

  useEffect(() => {
    engineRef.current?.setPath(pathNodeIds ?? [], pathEdgeIds ?? []);
  }, [data, pathNodeIds, pathEdgeIds]);

  useEffect(() => {
    const engine = engineRef.current;
    if (!engine) return;
    const desired = layout ?? "force";
    if (appliedLayoutRef.current === desired) return;
    engine.layout(desired);
    appliedLayoutRef.current = desired;
  }, [layout]);

  useEffect(() => {
    const engine = engineRef.current;
    if (!engine) return;
    engine.setTheme(createTheme(theme ?? {}));
  }, [theme]);

  useEffect(() => {
    engineRef.current?.setMinimap(minimap);
  }, [minimap]);

  useEffect(() => {
    const engine = engineRef.current;
    if (!engine) return;
    if (selectedIds && selectedIds.length > 0) {
      engine.setSelection(selectedIds, selectedId ?? selectedIds[selectedIds.length - 1]);
    } else {
      engine.setSelection([], null);
      engine.select(selectedId ?? null);
    }
  }, [selectedId, selectedIds, data]);

  /* Erişilebilir özet: görünürlük veya veri değişince yenilenir. */
  useEffect(() => {
    const engine = engineRef.current;
    if (!engine) return;
    setSummary(engine.accessibilitySummary(10));
  }, [data, visibleNodeIds, visibleEdgeIds, summaryDirty]);

  useImperativeHandle(
    ref,
    () => ({
      fit: () => {
        engineRef.current?.fit();
      },
      focus: (id: string, minZoom?: number) => {
        engineRef.current?.focus(id, minZoom);
      },
      runLayout: (name: GraphLayoutName) => {
        engineRef.current?.layout(name);
      },
      fitSelection: (ids: readonly string[]) => {
        engineRef.current?.fitNodes(ids);
      },
      exportPNG: async () => {
        const engine = engineRef.current;
        if (!engine) throw new Error("Grafik motoru henüz hazır değil.");
        return engine.exportPNG();
      },
      exportSVG: () => {
        const engine = engineRef.current;
        if (!engine) throw new Error("Grafik motoru henüz hazır değil.");
        return engine.exportSVG();
      },
      getCamera: () => engineRef.current?.getCamera() ?? { x: 0, y: 0, zoom: 1 },
      setSelection: (ids: readonly string[], primary?: string | null) => {
        engineRef.current?.setSelection(ids, primary ?? null);
      },
      getSelection: () => engineRef.current?.getSelectedIds() ?? [],
      focusNode: (id: string | null) => {
        engineRef.current?.focusNode(id);
      },
      zoomBy: (factor: number) => {
        engineRef.current?.zoomBy(factor);
      },
      getPositions: () => engineRef.current?.getPositions() ?? {},
    }),
    []
  );

  const summaryText =
    summary.length === 0
      ? "Görünür düğüm yok."
      : `En çok bağlantılı düğümler: ${summary
          .map((item) => `${item.label} (${item.degree} bağlantı)`)
          .join(", ")}.`;
  const focusedNode = focusedId
    ? data.nodes.find((node) => node.id === focusedId) ?? null
    : null;

  return (
    <div className={className} style={{ position: "relative", overflow: "hidden", ...style }}>
      <div ref={containerRef} className="h-full w-full" />
      <div className="sr-only" id="graph-canvas-summary">
        <p>Grafik erişilebilirlik özeti.</p>
        {summary.length === 0 ? (
          <p>Görünür düğüm yok.</p>
        ) : (
          <ul aria-label="En çok bağlantılı düğümler">
            {summary.map((item) => (
              <li key={item.id}>
                {item.label}: {item.degree} bağlantı
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="sr-only" id="graph-canvas-help">
        <p>
          Tab ile düğümler arasında gezinin, Enter ile seçin, Esc ile seçimi
          temizleyin. Shift veya Ctrl ile sürükleyerek kutu seçimi yapın. Sağ tık
          bağlam menüsünü açar.
        </p>
      </div>
      <p className="sr-only" role="status" aria-live="polite">
        {focusedNode ? `Odak: ${focusedNode.label}` : ""}
      </p>
    </div>
  );
});

export default GraphCanvas;
