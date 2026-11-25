"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Download,
  ExternalLink,
  FileCode2,
  Focus,
  Image as ImageIcon,
  Info,
  Layers,
  RotateCcw,
  Search,
  X,
} from "lucide-react";
import { api, type GraphData } from "../../../lib/api";
import {
  GraphCanvas,
  GraphContextMenu,
  type GraphCanvasHandle,
  type GraphContextTarget,
  type GraphEdgeHover,
  type GraphLayoutName,
  buildContextMenu,
  collapseGraphData,
  hubIdOfProxy,
  neighborIds,
  type ContextMenuAction,
  type HubGroup,
} from "../../../lib/graph-engine";
import { platformMeta } from "./platform-meta";
import { edgeTypeLabel } from "./vocabulary";

/* ------------------------------------------------------------------ */
/* Sabitler                                                            */
/* ------------------------------------------------------------------ */

/** Düğüm konturu platform vurgu rengidir; siyah zeminde okunur ton. */
function platformColor(platform: string): string {
  return platformMeta(platform).accent;
}

/** Küme kimliğinden deterministik renk — aynı küme her açılışta aynı renk. */
const CLUSTER_PALETTE = [
  "#60a5fa",
  "#2dd4bf",
  "#fbbf24",
  "#c084fc",
  "#fb7185",
  "#a3e635",
  "#38bdf8",
  "#e879f9",
];

function clusterColor(clusterId: string | null): string | null {
  if (!clusterId) return null;
  let hash = 0;
  for (let index = 0; index < clusterId.length; index += 1) {
    hash = (hash * 31 + clusterId.charCodeAt(index)) >>> 0;
  }
  return CLUSTER_PALETTE[hash % CLUSTER_PALETTE.length];
}

/** Kenar tipi renkleri — siyah zeminde okunur açık tonlar. */
const EDGE_TYPE_COLORS: Record<string, string> = {
  same_person: "#6fbf8e",
  cross_link: "#84aad1",
  has_account: "#8d96a2",
  mentions: "#d9a441",
  owned_domain: "#b79ce0",
};

const EDGE_TYPE_FALLBACK = "#8d96a2";
const MUTED_DOT = "#4e5660";

const EDGE_TYPE_LABEL = edgeTypeLabel;

const LAYOUTS: { value: GraphLayoutName; label: string; hint: string }[] = [
  { value: "force", label: "Force", hint: "Kuvvet tabanlı yerleşim" },
  { value: "radial", label: "Hubs", hint: "Bağlantı sayısı yüksek düğümler merkezde" },
  { value: "hierarchical", label: "Hiyerarşi", hint: "BFS derinliğine göre katmanlı yerleşim" },
  { value: "circle", label: "Circle", hint: "Dairesel yerleşim" },
  { value: "grid", label: "Grid", hint: "Izgara yerleşim" },
];

const LAYOUT_VALUES = new Set<string>(LAYOUTS.map((layout) => layout.value));

/* ------------------------------------------------------------------ */
/* Tipler                                                              */
/* ------------------------------------------------------------------ */

interface GNode {
  id: string;
  label: string;
  type: string;
  platform: string;
  url: string | null;
  score: number;
  attributes: Record<string, unknown>;
  clusterId: string | null;
  clusterSize: number;
  clusterConfidence: number;
  clusterPlatforms: string[];
}

interface GCluster {
  id: string;
  size: number;
  platforms: string[];
  max_confidence: number;
  edge_count: number;
  evidence_count: number;
  reasons: string[];
}

interface GEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  confidence: number;
  reason: string;
}

interface GraphPrefs {
  minConfidence: number;
  minScore: number;
  connectedOnly: boolean;
  clusterId: string | null;
  platforms: string[];
  edgeTypes: string[];
  layout: GraphLayoutName;
  colorMode: "platform" | "cluster";
  collapseHubs: boolean;
}

interface HoverEdge {
  id: string;
  x: number;
  y: number;
  /** Tıklanarak sabitlendiyse fare çıkışında kaybolmaz. */
  pinned: boolean;
}

const DEFAULT_PREFS: GraphPrefs = {
  minConfidence: 0,
  minScore: 0,
  connectedOnly: false,
  clusterId: null,
  platforms: [],
  edgeTypes: [],
  layout: "force",
  colorMode: "platform",
  collapseHubs: true,
};

const prefsKey = (caseId: string) => `showis:graph:${caseId}`;

function readPrefs(caseId: string): GraphPrefs | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(prefsKey(caseId));
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<GraphPrefs>;
    const layout: GraphLayoutName =
      typeof parsed.layout === "string" && LAYOUT_VALUES.has(parsed.layout)
        ? parsed.layout
        : DEFAULT_PREFS.layout;
    const min = Number(parsed.minConfidence);
    const minScore = Number(parsed.minScore);
    const clusterId =
      typeof parsed.clusterId === "string" && parsed.clusterId.trim()
        ? parsed.clusterId.trim()
        : null;
    return {
      minConfidence: Number.isFinite(min) ? Math.min(1, Math.max(0, min)) : 0,
      minScore: Number.isFinite(minScore) ? Math.min(1, Math.max(0, minScore)) : 0,
      connectedOnly: parsed.connectedOnly === true,
      clusterId,
      platforms: Array.isArray(parsed.platforms) ? parsed.platforms.map(String) : [],
      edgeTypes: Array.isArray(parsed.edgeTypes) ? parsed.edgeTypes.map(String) : [],
      layout,
      colorMode: parsed.colorMode === "cluster" ? "cluster" : "platform",
      collapseHubs: parsed.collapseHubs !== false,
    };
  } catch {
    return null;
  }
}

function writePrefs(caseId: string, prefs: GraphPrefs) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(prefsKey(caseId), JSON.stringify(prefs));
}

/** Yalnız http(s) URL'lerini tıklanabilir yap (javascript:/data: XSS koruması). */
function safeUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url, "https://example.invalid");
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
    return parsed.toString();
  } catch {
    return null;
  }
}

function attrText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") return value.length > 400 ? `${value.slice(0, 400)}…` : value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map((v) => attrText(v)).join(", ");
  try {
    const text = JSON.stringify(value);
    return text.length > 240 ? `${text.slice(0, 240)}…` : text;
  } catch {
    return "—";
  }
}

const ATTR_PRIORITY = [
  "name",
  "display_name",
  "username",
  "bio",
  "location",
  "company",
  "school",
  "created_at",
  "followers",
  "public_repos",
  "avatar_url",
  "avatar_sha256",
];

function sortedAttributes(attrs: Record<string, unknown>): [string, unknown][] {
  const entries = Object.entries(attrs).filter(([, v]) => v !== null && v !== undefined && v !== "");
  const rank = (key: string) => {
    const i = ATTR_PRIORITY.indexOf(key);
    return i === -1 ? ATTR_PRIORITY.length : i;
  };
  return entries.sort((a, b) => {
    const d = rank(a[0]) - rank(b[0]);
    return d !== 0 ? d : a[0].localeCompare(b[0]);
  });
}

function downloadHref(filename: string, href: string) {
  const a = document.createElement("a");
  a.href = href;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function downloadBlob(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  downloadHref(filename, url);
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function xmlEscape(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

/* ------------------------------------------------------------------ */
/* Bileşen                                                             */
/* ------------------------------------------------------------------ */

export default function CaseGraph({ caseId }: { caseId: string }) {
  const canvasRef = useRef<GraphCanvasHandle>(null);
  const canvasBoxRef = useRef<HTMLDivElement>(null);
  const prefsReadyRef = useRef(false);
  const panelRef = useRef<HTMLElement>(null);

  const [error, setError] = useState<string | null>(null);
  const [nodes, setNodes] = useState<GNode[]>([]);
  const [edges, setEdges] = useState<GEdge[]>([]);
  const [clusters, setClusters] = useState<GCluster[]>([]);
  const [loading, setLoading] = useState(true);
  const [prefs, setPrefs] = useState<GraphPrefs>(DEFAULT_PREFS);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [contextMenu, setContextMenu] = useState<GraphContextTarget | null>(null);
  /** Kullanıcı genişletse de daraltmayı elle açtığı hub'lar. */
  const [expandedHubs, setExpandedHubs] = useState<string[]>([]);
  /** "Komşuları genişlet" ve "Gizle" için geçici görünürlük kapsamı. */
  const [scope, setScope] = useState<{
    expand: string[];
    hide: string[];
    only: string[] | null;
  }>({
    expand: [],
    hide: [],
    only: null,
  });
  const [hover, setHover] = useState<HoverEdge | null>(null);
  const [layoutMs, setLayoutMs] = useState(0);
  const [nodeQuery, setNodeQuery] = useState("");
  const [pathFrom, setPathFrom] = useState("");
  const [pathTo, setPathTo] = useState("");
  const [pathNodeIds, setPathNodeIds] = useState<string[]>([]);
  const [pathEdgeIds, setPathEdgeIds] = useState<string[]>([]);
  const [pathError, setPathError] = useState<string | null>(null);
  /** Seçim sonrası kısa geri bildirim; canlı bölge ile duyurulur. */
  const [graphNotice, setGraphNotice] = useState("");
  /** Dışa aktarma hatası — boşsa hata yok. */
  const [exportError, setExportError] = useState<{ message: string; detail?: string } | null>(
    null
  );

  /* --- tercihleri yükle (yalnız istemci); URL'deki küme önceliklidir --- */
  useEffect(() => {
    const stored = readPrefs(caseId);
    const fromUrl =
      typeof window !== "undefined"
        ? new URLSearchParams(window.location.search).get("cluster")
        : null;
    const next = stored ?? DEFAULT_PREFS;
    if (fromUrl && fromUrl.trim()) {
      setPrefs({ ...next, clusterId: fromUrl.trim() });
    } else if (stored) {
      setPrefs(stored);
    }
    prefsReadyRef.current = true;
  }, [caseId]);

  const updatePrefs = useCallback(
    (patch: Partial<GraphPrefs>) => {
      setPrefs((prev) => {
        const next = { ...prev, ...patch };
        if (prefsReadyRef.current) writePrefs(caseId, next);
        return next;
      });
    },
    [caseId]
  );

  /* --- veriyi getir --- */
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getGraph(caseId)
      .then((payload: GraphData) => {
        if (cancelled) return;
        const gNodes: GNode[] = (payload.nodes ?? []).map((n) => ({
          id: String(n.data.id),
          label: String(n.data.label ?? n.data.id),
          type: String(n.data.type ?? "unknown"),
          platform: String(n.data.platform ?? "unknown"),
          url: n.data.url ?? null,
          score: Number(n.data.score ?? 0),
          attributes: (n.data.attributes as Record<string, unknown>) ?? {},
          clusterId: n.data.cluster_id ? String(n.data.cluster_id) : null,
          clusterSize: Number(n.data.cluster_size ?? 1),
          clusterConfidence: Number(n.data.cluster_confidence ?? 0),
          clusterPlatforms: Array.isArray(n.data.cluster_platforms)
            ? n.data.cluster_platforms.map(String)
            : [],
        }));
        const gEdges: GEdge[] = (payload.edges ?? []).map((e) => ({
          id: String(e.data.id),
          source: String(e.data.source),
          target: String(e.data.target),
          type: String(e.data.type ?? e.data.label ?? "unknown"),
          confidence: Number(e.data.confidence ?? 0),
          reason: String(e.data.reason ?? ""),
        }));
        const gClusters: GCluster[] = (payload.clusters ?? []).map((cluster) => ({
          id: String(cluster.id),
          size: Number(cluster.size ?? 0),
          platforms: Array.isArray(cluster.platforms) ? cluster.platforms.map(String) : [],
          max_confidence: Number(cluster.max_confidence ?? 0),
          edge_count: Number(cluster.edge_count ?? 0),
          evidence_count: Number(cluster.evidence_count ?? 0),
          reasons: Array.isArray(cluster.reasons) ? cluster.reasons.map(String) : [],
        }));
        setNodes(gNodes);
        setEdges(gEdges);
        setClusters(gClusters);
      })
      .catch((e: Error) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [caseId]);

  /* --- filtre yardımcıları --- */
  const connectedNodeIds = useMemo(() => {
    const ids = new Set<string>();
    edges.forEach((edge) => {
      ids.add(edge.source);
      ids.add(edge.target);
    });
    return ids;
  }, [edges]);

  const visibleNodeIds = useMemo(() => {
    const platformSet = new Set(prefs.platforms);
    const base = nodes
      .filter((node) => platformSet.size === 0 || platformSet.has(node.platform))
      .filter((node) => node.score >= prefs.minScore)
      .filter((node) => !prefs.clusterId || node.clusterId === prefs.clusterId)
      .filter((node) => !prefs.connectedOnly || connectedNodeIds.has(node.id))
      .map((node) => node.id);
    let result = new Set(base);

    // "Komşuları genişlet": filtrenin dışında kalan komşular geri gelir.
    if (scope.expand.length > 0) {
      const adjacency = new Map<string, string[]>();
      for (const edge of edges) {
        if (!adjacency.has(edge.source)) adjacency.set(edge.source, []);
        if (!adjacency.has(edge.target)) adjacency.set(edge.target, []);
        adjacency.get(edge.source)!.push(edge.target);
        adjacency.get(edge.target)!.push(edge.source);
      }
      for (const id of scope.expand) {
        result.add(id);
        for (const neighbor of adjacency.get(id) ?? []) result.add(neighbor);
      }
    }

    // "Yalnız komşuları göster": filtreleri aşan açık kapsam.
    if (scope.only) {
      result = new Set(scope.only);
    }
    for (const id of scope.hide) result.delete(id);
    return result;
  }, [
    nodes,
    prefs.platforms,
    prefs.minScore,
    prefs.clusterId,
    prefs.connectedOnly,
    connectedNodeIds,
    edges,
    scope,
  ]);

  const visibleEdges = useMemo(() => {
    const typeSet = new Set(prefs.edgeTypes);
    return edges.filter(
      (e) =>
        (typeSet.size === 0 || typeSet.has(e.type)) &&
        e.confidence >= prefs.minConfidence &&
        visibleNodeIds.has(e.source) &&
        visibleNodeIds.has(e.target)
    );
  }, [edges, prefs.edgeTypes, prefs.minConfidence, visibleNodeIds]);

  const visibleEdgeIds = useMemo(() => new Set(visibleEdges.map((e) => e.id)), [visibleEdges]);

  const platformCounts = useMemo(() => {
    const map = new Map<string, number>();
    nodes.forEach((n) => map.set(n.platform, (map.get(n.platform) ?? 0) + 1));
    return [...map.entries()].sort((a, b) => b[1] - a[1]);
  }, [nodes]);

  const edgeTypeCounts = useMemo(() => {
    const map = new Map<string, number>();
    edges.forEach((e) => map.set(e.type, (map.get(e.type) ?? 0) + 1));
    return [...map.entries()].sort((a, b) => b[1] - a[1]);
  }, [edges]);

  /* --- motor verisi ve renkleri --- */
  const baseNodes = useMemo(
    () =>
      nodes.map((node) => ({
        id: node.id,
        label: node.label,
        kind: node.type,
        // Cytoscape'teki 20-44 px çapa denk gelir.
        weight: 4 + Math.min(1, Math.max(0, node.score)) * 40,
        cluster: node.clusterId,
      })),
    [nodes]
  );

  const baseEdges = useMemo(
    () =>
      edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        type: edge.type,
        directed: true,
        // 0.6-3.5 px kenar kalınlığı.
        weight: 0.5 + Math.min(1, Math.max(0, edge.confidence)) * 7,
      })),
    [edges]
  );

  /**
   * Hub daraltma tüm grafik üzerinde hesaplanır; görünürlük filtresi "+N"
   * çiplerine eşlenir. Böylece filtre değişimi kamerayı ve yerleşimi bozmaz.
   */
  const collapsed = useMemo(() => {
    if (!prefs.collapseHubs) {
      return {
        data: { nodes: baseNodes, edges: baseEdges },
        groups: new Map<string, HubGroup>(),
      };
    }
    return collapseGraphData(
      { nodes: baseNodes, edges: baseEdges },
      { expandedHubIds: new Set(expandedHubs) }
    );
  }, [baseNodes, baseEdges, prefs.collapseHubs, expandedHubs]);

  const graphData = collapsed.data;
  const hubGroups = collapsed.groups;

  /**
   * Filtre görünürlüğü daraltılmış kimliklere eşlenir: bir "+N" çipi, hub'ı
   * veya en az bir gizli yaprağı görünürse çizilir.
   */
  const canvasNodeIds = useMemo(() => {
    const ids = new Set<string>();
    for (const node of graphData.nodes) {
      if (visibleNodeIds.has(node.id)) {
        ids.add(node.id);
        continue;
      }
      const group = hubGroups.get(node.id);
      if (!group) continue;
      if (
        visibleNodeIds.has(group.hubId) ||
        group.leafIds.some((id) => visibleNodeIds.has(id))
      ) {
        ids.add(node.id);
      }
    }
    return ids;
  }, [graphData, hubGroups, visibleNodeIds]);

  const canvasEdgeIds = useMemo(() => {
    const ids = new Set<string>();
    for (const edge of graphData.edges) {
      if (canvasNodeIds.has(edge.source) && canvasNodeIds.has(edge.target)) {
        ids.add(edge.id);
      }
    }
    return ids;
  }, [graphData, canvasNodeIds]);

  const nodeColors = useMemo(() => {
    const colors: Record<string, string> = {};
    for (const node of nodes) {
      colors[node.id] =
        (prefs.colorMode === "cluster" ? clusterColor(node.clusterId) : null) ??
        platformColor(node.platform);
    }
    // "+N" çipi hub'ın rengini taşır; türü kendi kimliğinde kalır.
    for (const group of hubGroups.values()) {
      if (colors[group.hubId]) colors[group.proxyId] = colors[group.hubId];
    }
    return colors;
  }, [nodes, prefs.colorMode, hubGroups]);

  /* --- etkileşim --- */
  const handleSelect = useCallback((id: string | null) => {
    setSelectedId(id);
    setHover(null);
  }, []);

  const handleSelectionChange = useCallback(
    (ids: string[], primary: string | null) => {
      setHover(null);
      // "+N" düğümü tıklanınca hub açılır; seçim hub'ın kendisine geçer.
      const proxyId = ids.find((id) => hubIdOfProxy(id) !== null);
      if (proxyId) {
        const hubId = hubIdOfProxy(proxyId) as string;
        setExpandedHubs((previous) =>
          previous.includes(hubId) ? previous : [...previous, hubId]
        );
        setGraphNotice(`Hub açıldı: ${proxyId} → ${hubId}`);
        setSelectedIds([hubId]);
        setSelectedId(hubId);
        canvasRef.current?.setSelection([hubId], hubId);
        canvasRef.current?.focus(hubId);
        return;
      }
      setSelectedIds(ids);
      setSelectedId(primary);
    },
    []
  );

  const handleContextMenu = useCallback((target: GraphContextTarget) => {
    setContextMenu(target);
    setHover(null);
  }, []);

  const handleEdgeHover = useCallback((event: GraphEdgeHover) => {
    setHover((previous) => {
      if (previous?.pinned) return previous;
      if (!event.id) return null;
      return { id: event.id, x: event.x, y: event.y, pinned: false };
    });
  }, []);

  const handleEdgeClick = useCallback((event: GraphEdgeHover) => {
    if (!event.id) return;
    setHover({ id: event.id, x: event.x, y: event.y, pinned: true });
  }, []);

  const handleLayout = useCallback((info: { ms: number }) => {
    setLayoutMs(info.ms);
  }, []);

  /* --- filtre değişince eski yol vurgusu anlamsızlaşır --- */
  useEffect(() => {
    setPathNodeIds([]);
    setPathEdgeIds([]);
    setPathError(null);
  }, [
    prefs.minConfidence,
    prefs.minScore,
    prefs.connectedOnly,
    prefs.clusterId,
    prefs.platforms,
    prefs.edgeTypes,
  ]);

  /* --- Esc: bağlam menüsü, seçim ve paneli sırayla kapat --- */
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (contextMenu) {
        setContextMenu(null);
        return;
      }
      if (selectedIds.length > 0) {
        canvasRef.current?.setSelection([], null);
        setSelectedIds([]);
        setSelectedId(null);
        return;
      }
      setSelectedId(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [contextMenu, selectedIds.length]);

  /* --- panel yalnız tek seçimde odaklanır --- */
  useEffect(() => {
    if (!selectedId || selectedIds.length > 1) return;
    panelRef.current?.focus();
  }, [selectedId, selectedIds.length]);

  /* --- türetilmiş seçim verisi --- */
  const selectedNode = useMemo(
    () => nodes.find((n) => n.id === selectedId) ?? null,
    [nodes, selectedId]
  );

  const selectedCluster = useMemo(
    () =>
      selectedNode?.clusterId
        ? clusters.find((cluster) => cluster.id === selectedNode.clusterId) ?? null
        : null,
    [clusters, selectedNode]
  );

  const visibleNodes = useMemo(
    () =>
      nodes
        .filter((node) => canvasNodeIds.has(node.id))
        .sort((a, b) => b.score - a.score || a.label.localeCompare(b.label)),
    [nodes, canvasNodeIds]
  );

  const hoverEdge = useMemo(
    () => (hover ? edges.find((edge) => edge.id === hover.id) ?? null : null),
    [edges, hover]
  );

  const linkedEdges = useMemo(() => {
    if (!selectedId) return [];
    return edges
      .filter((e) => e.source === selectedId || e.target === selectedId)
      .filter((e) => visibleEdgeIds.has(e.id))
      .map((e) => {
        const outgoing = e.source === selectedId;
        const otherId = outgoing ? e.target : e.source;
        return {
          edge: e,
          outgoing,
          other: nodes.find((n) => n.id === otherId) ?? null,
        };
      })
      .sort((a, b) => b.edge.confidence - a.edge.confidence);
  }, [edges, nodes, selectedId, visibleEdgeIds]);

  const hiddenLinkedCount = useMemo(() => {
    if (!selectedId) return 0;
    return edges.filter(
      (e) => (e.source === selectedId || e.target === selectedId) && !visibleEdgeIds.has(e.id)
    ).length;
  }, [edges, selectedId, visibleEdgeIds]);

  const selectedFindingId = selectedNode ? `finding:${selectedNode.id}` : null;

  /* --- dışa aktarma --- */
  const exportBase = `showis-graph-${caseId.slice(0, 8)}`;

  const exportFailed = (kind: string, detail?: string) => {
    setExportError({
      message: `${kind} dışa aktarımı başarısız oldu. Tarayıcıyı ve ağ bağlantısını kontrol edip yeniden deneyin.`,
      detail,
    });
  };

  const runExport = (kind: string, fn: () => void | Promise<void>) => {
    setExportError(null);
    try {
      void Promise.resolve(fn()).catch((e: unknown) =>
        exportFailed(kind, e instanceof Error ? e.message : undefined)
      );
    } catch (e: unknown) {
      exportFailed(kind, e instanceof Error ? e.message : undefined);
    }
  };

  const exportPng = () => {
    const handle = canvasRef.current;
    if (!handle) return;
    return handle.exportPNG().then((blob) => downloadBlob(`${exportBase}.png`, blob));
  };

  const exportSvg = () => {
    const handle = canvasRef.current;
    if (!handle) return;
    const svg = handle.exportSVG();
    downloadBlob(`${exportBase}.svg`, new Blob([svg], { type: "image/svg+xml;charset=utf-8" }));
  };

  const filteredPayload = () => {
    return {
      exported_at: new Date().toISOString(),
      case_id: caseId,
      filters: prefs,
      nodes: nodes.filter((n) => visibleNodeIds.has(n.id)),
      edges: visibleEdges,
    };
  };

  const exportJson = () => {
    const blob = new Blob([JSON.stringify(filteredPayload(), null, 2)], {
      type: "application/json",
    });
    downloadBlob(`${exportBase}.json`, blob);
  };

  const exportGexf = () => {
    const payload = filteredPayload();
    const lines: string[] = [
      '<?xml version="1.0" encoding="UTF-8"?>',
      '<gexf xmlns="http://www.gexf.net/1.2draft" version="1.2">',
      '  <meta><creator>Showis</creator></meta>',
      '  <graph mode="static" defaultedgetype="directed">',
      "    <attributes class=\"node\">",
      '      <attribute id="platform" title="platform" type="string"/>',
      '      <attribute id="type" title="type" type="string"/>',
      '      <attribute id="score" title="score" type="double"/>',
      "    </attributes>",
      "    <attributes class=\"edge\">",
      '      <attribute id="etype" title="type" type="string"/>',
      '      <attribute id="confidence" title="confidence" type="double"/>',
      '      <attribute id="reason" title="reason" type="string"/>',
      "    </attributes>",
      "    <nodes>",
      ...payload.nodes.map(
        (n) =>
          `      <node id="${xmlEscape(n.id)}" label="${xmlEscape(n.label)}"><attvalues>` +
          `<attvalue for="platform" value="${xmlEscape(n.platform)}"/>` +
          `<attvalue for="type" value="${xmlEscape(n.type)}"/>` +
          `<attvalue for="score" value="${n.score}"/>` +
          `</attvalues></node>`
      ),
      "    </nodes>",
      "    <edges>",
      ...payload.edges.map(
        (e) =>
          `      <edge id="${xmlEscape(e.id)}" source="${xmlEscape(e.source)}" target="${xmlEscape(
            e.target
          )}" weight="${e.confidence}" label="${xmlEscape(EDGE_TYPE_LABEL(e.type))}"><attvalues>` +
          `<attvalue for="etype" value="${xmlEscape(e.type)}"/>` +
          `<attvalue for="confidence" value="${e.confidence}"/>` +
          `<attvalue for="reason" value="${xmlEscape(e.reason)}"/>` +
          `</attvalues></edge>`
      ),
      "    </edges>",
      "  </graph>",
      "</gexf>",
    ];
    downloadBlob(`${exportBase}.gexf`, new Blob([lines.join("\n")], { type: "application/xml" }));
  };

  const focusSelected = () => {
    const ids = selectedIds.length > 0 ? selectedIds : selectedId ? [selectedId] : [];
    if (ids.length === 0) return;
    if (ids.length === 1) canvasRef.current?.focus(ids[0]);
    else canvasRef.current?.fitSelection(ids);
  };

  const jumpToNode = (nodeId: string) => {
    setSelectedIds([nodeId]);
    setSelectedId(nodeId);
    canvasRef.current?.setSelection([nodeId], nodeId);
    canvasRef.current?.focus(nodeId, 1.25);
  };

  const jumpToSearch = () => {
    const term = nodeQuery.trim().toLocaleLowerCase("tr");
    if (!term) return;
    const match =
      visibleNodes.find((node) => node.label.toLocaleLowerCase("tr") === term) ??
      visibleNodes.find((node) => node.id.toLocaleLowerCase("tr") === term) ??
      visibleNodes.find((node) => node.label.toLocaleLowerCase("tr").includes(term));
    if (match) {
      setExportError(null);
      jumpToNode(match.id);
    } else {
      setExportError({
        message: "Aramayla eşleşen görünür düğüm bulunamadı.",
        detail: `Aranan: ${nodeQuery}`,
      });
    }
  };

  const fitVisible = () => {
    canvasRef.current?.fit();
  };

  const findShortestPath = () => {
    setPathError(null);
    if (!pathFrom || !pathTo) {
      setPathError("Bağlantı yolu için iki düğüm seçin.");
      return;
    }
    if (pathFrom === pathTo) {
      setPathError("Aynı düğüm seçildi; iki farklı hesap seçin.");
      return;
    }

    const adjacency = new Map<string, { to: string; edgeId: string }[]>();
    for (const edge of visibleEdges) {
      if (!adjacency.has(edge.source)) adjacency.set(edge.source, []);
      if (!adjacency.has(edge.target)) adjacency.set(edge.target, []);
      adjacency.get(edge.source)!.push({ to: edge.target, edgeId: edge.id });
      adjacency.get(edge.target)!.push({ to: edge.source, edgeId: edge.id });
    }

    const queue: string[] = [pathFrom];
    const previous = new Map<string, { node: string; edgeId: string } | null>();
    previous.set(pathFrom, null);
    while (queue.length > 0) {
      const current = queue.shift()!;
      if (current === pathTo) break;
      for (const step of adjacency.get(current) ?? []) {
        if (previous.has(step.to)) continue;
        previous.set(step.to, { node: current, edgeId: step.edgeId });
        queue.push(step.to);
      }
    }

    if (!previous.has(pathTo)) {
      setPathNodeIds([]);
      setPathEdgeIds([]);
      setPathError(
        "Görünür kenarlar üzerinden bağlantı yok. Filtreleri gevşetip yeniden deneyin."
      );
      return;
    }

    const nodePath: string[] = [];
    const edgePath: string[] = [];
    let cursor: string | null = pathTo;
    while (cursor) {
      nodePath.push(cursor);
      const step = previous.get(cursor);
      if (!step) break;
      edgePath.push(step.edgeId);
      cursor = step.node;
    }
    nodePath.reverse();
    edgePath.reverse();
    setPathNodeIds(nodePath);
    setPathEdgeIds(edgePath);
  };

  const clearPath = () => {
    setPathNodeIds([]);
    setPathEdgeIds([]);
    setPathError(null);
  };

  /* --- sağ tık menüsü eylemleri --- */

  /** Menünün hedef kimliği; "+N" ise hub'ın kendi kimliğine çözülür. */
  const menuTargetId = contextMenu
    ? contextMenu.id
      ? hubIdOfProxy(contextMenu.id) ?? contextMenu.id
      : null
    : null;

  const menuItems = useMemo(
    () =>
      buildContextMenu({
        targetId: menuTargetId,
        selection: new Set(selectedIds),
        // Komşu sayısı ham grafikten: daraltılmış yapraklar da sayılır.
        edges,
        visibleNodeCount: canvasNodeIds.size,
      }),
    [menuTargetId, selectedIds, edges, canvasNodeIds]
  );

  const effectiveSelectionForMenu = (targetId: string | null): string[] => {
    if (!targetId) return selectedIds;
    return selectedIds.includes(targetId) ? selectedIds : [targetId];
  };

  /** Seçimdeki "+N" çiplerini gerçek hub/yaprak kimliklerine açar. */
  const expandSelectionIds = (ids: readonly string[]): string[] => {
    const result = new Set<string>();
    for (const id of ids) {
      const group = hubGroups.get(id);
      if (group) {
        result.add(group.hubId);
        for (const leaf of group.leafIds) result.add(leaf);
      } else {
        result.add(id);
      }
    }
    return [...result];
  };

  /** Hub daraltılmışsa, komşularını görmek için önce hub açılır. */
  const revealHubLeaves = (targetId: string | null) => {
    if (!targetId) return;
    const group = [...hubGroups.values()].find((item) => item.hubId === targetId);
    if (!group) return;
    setExpandedHubs((previous) =>
      previous.includes(targetId) ? previous : [...previous, targetId]
    );
  };

  const runContextAction = (action: ContextMenuAction) => {
    const targetId = menuTargetId;
    const selection = effectiveSelectionForMenu(targetId);
    const targetNode = targetId ? nodes.find((node) => node.id === targetId) ?? null : null;

    switch (action) {
      case "expand-neighbors": {
        if (!targetId) return;
        const group = [...hubGroups.values()].find((item) => item.hubId === targetId);
        const neighbors = group
          ? group.leafIds
          : neighborIds(targetId, edges);
        setScope((previous) => ({
          expand: [...new Set([...previous.expand, targetId, ...neighbors])],
          hide: previous.hide.filter(
            (id) => id !== targetId && !neighbors.includes(id)
          ),
          only: null,
        }));
        revealHubLeaves(targetId);
        setGraphNotice(
          `${targetNode?.label ?? targetId} ve ${neighbors.length} komşusu genişletildi.`
        );
        setSelectedIds([targetId]);
        setSelectedId(targetId);
        break;
      }
      case "only-neighbors": {
        if (!targetId) return;
        const group = [...hubGroups.values()].find((item) => item.hubId === targetId);
        const neighbors = group ? group.leafIds : neighborIds(targetId, edges);
        setScope({ expand: [], hide: [], only: [targetId, ...neighbors] });
        revealHubLeaves(targetId);
        setGraphNotice(
          `Yalnız ${targetNode?.label ?? targetId} ve ${neighbors.length} komşusu gösteriliyor.`
        );
        break;
      }
      case "hide": {
        if (!targetId) return;
        const before = canvasNodeIds.size;
        const hidden = new Set(expandSelectionIds(selection));
        setScope((previous) => ({
          ...previous,
          hide: [...new Set([...previous.hide, ...hidden])],
        }));
        // Aynı anda seçim temizlenir; gizlenenler seçili kalmasın.
        setSelectedIds([]);
        setSelectedId(null);
        canvasRef.current?.setSelection([], null);
        setGraphNotice(
          `${hidden.size} düğüm gizlendi. Görünür: ${Math.max(0, before - hidden.size)}.`
        );
        break;
      }
      case "path-from": {
        if (!targetId) return;
        setPathFrom(targetId);
        setPathError(null);
        setGraphNotice(`Yol başlangıcı: ${targetNode?.label ?? targetId}`);
        break;
      }
      case "path-to": {
        if (!targetId) return;
        setPathTo(targetId);
        setPathError(null);
        setGraphNotice(`Yol hedefi: ${targetNode?.label ?? targetId}`);
        break;
      }
      case "copy-label": {
        if (!targetNode) return;
        const copy = async () => {
          try {
            await navigator.clipboard.writeText(targetNode.label);
            setGraphNotice(`Etiket kopyalandı: ${targetNode.label}`);
          } catch {
            setGraphNotice("Etiket kopyalanamadı; tarayıcı izni gerekiyor.");
          }
        };
        void copy();
        break;
      }
      case "focus-selection": {
        if (selection.length === 0) return;
        if (selection.length === 1) canvasRef.current?.focus(selection[0]);
        else canvasRef.current?.fitSelection(selection);
        setGraphNotice(
          selection.length === 1
            ? "Seçili düğüme odaklanıldı."
            : `${selection.length} seçili düğüme odaklanıldı.`
        );
        break;
      }
      case "clear-selection": {
        setSelectedIds([]);
        setSelectedId(null);
        canvasRef.current?.setSelection([], null);
        break;
      }
      default:
        break;
    }
    setContextMenu(null);
  };

  /* --- erken çıkışlar --- */
  if (error)
    return (
      <p role="alert" className="rounded-[6px] border border-danger-line bg-danger-soft px-4 py-3 text-[13px] text-danger">
        Grafik yüklenemedi: {error}
      </p>
    );
  if (!loading && nodes.length === 0) {
    return (
      <p className="rounded-[6px] border border-line bg-sunken px-4 py-3 text-[13px] text-ink-3">
        Henüz kayıt veya bağlantı yok. Önce bir tarama çalıştırın.
      </p>
    );
  }

  const togglePlatform = (platform: string) => {
    const next = prefs.platforms.includes(platform)
      ? prefs.platforms.filter((p) => p !== platform)
      : [...prefs.platforms, platform];
    updatePrefs({ platforms: next });
  };

  const toggleEdgeType = (type: string) => {
    const next = prefs.edgeTypes.includes(type)
      ? prefs.edgeTypes.filter((t) => t !== type)
      : [...prefs.edgeTypes, type];
    updatePrefs({ edgeTypes: next });
  };

  const resetFilters = () => {
    updatePrefs({
      minConfidence: 0,
      minScore: 0,
      connectedOnly: false,
      clusterId: null,
      platforms: [],
      edgeTypes: [],
    });
    setScope({ expand: [], hide: [], only: null });
  };

  const hiddenEdges = edges.length - visibleEdges.length;
  const multiClusters = clusters.filter((cluster) => cluster.size > 1);

  const edgeColor = (type: string) => EDGE_TYPE_COLORS[type] ?? EDGE_TYPE_FALLBACK;

  return (
    <div className="space-y-5">
      {/* ---------- Araç çubuğu ---------- */}
      <section className="panel flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2">
        <div className="flex w-full flex-wrap items-center gap-x-5 gap-y-3">
          <div className="flex items-center gap-2">
            <span className="text-[12px] text-ink-3">Yerleşim</span>
            <div
              role="group"
              aria-label="Grafik yerleşimi"
              className="flex items-center gap-0.5 rounded-[6px] border border-line-2 bg-sunken p-0.5"
            >
              {LAYOUTS.map((l) => (
                <button
                  key={l.value}
                  type="button"
                  aria-pressed={prefs.layout === l.value}
                  aria-label={`${l.label} yerleşimi`}
                  title={l.hint}
                  onClick={() => updatePrefs({ layout: l.value })}
                  className={`rounded-[4px] px-2.5 py-1 text-[12px] transition-colors ${
                    prefs.layout === l.value
                      ? "bg-surface font-medium text-ink shadow-scrim"
                      : "text-ink-3 hover:text-ink"
                  }`}
                >
                  {l.label}
                </button>
              ))}
            </div>
            <button
              type="button"
              onClick={() => canvasRef.current?.runLayout(prefs.layout)}
              className="btn-quiet btn-sm"
              aria-label="Grafiği yeniden yerleştir"
              title="Yeniden yerleştir"
            >
              <RotateCcw size={12} aria-hidden />
            </button>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-[12px] text-ink-3">Renk</span>
            <div
              role="group"
              aria-label="Düğüm renklendirme"
              className="flex items-center gap-0.5 rounded-[6px] border border-line-2 bg-sunken p-0.5"
            >
              {(["platform", "cluster"] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  aria-pressed={prefs.colorMode === mode}
                  onClick={() => updatePrefs({ colorMode: mode })}
                  className={`rounded-[4px] px-2 py-1 text-[12px] transition-colors ${
                    prefs.colorMode === mode
                      ? "bg-surface font-medium text-ink shadow-scrim"
                      : "text-ink-3 hover:text-ink"
                  }`}
                >
                  {mode === "platform" ? "Platform" : "Küme"}
                </button>
              ))}
            </div>
          </div>

          <div className="relative w-full sm:w-56">
            <Search
              size={12}
              aria-hidden
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-3"
            />
            <input
              type="search"
              list="graph-node-options"
              value={nodeQuery}
              onChange={(e) => setNodeQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  jumpToSearch();
                }
              }}
              placeholder="Düğüm ara (Enter)"
              aria-label="Grafikte düğüm ara"
              className="input w-full py-1 pl-7 text-[12px]"
            />
            <datalist id="graph-node-options">
              {visibleNodes.slice(0, 200).map((node) => (
                <option key={node.id} value={node.label} label={node.platform} />
              ))}
            </datalist>
          </div>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            <span className="numeric text-ink-3" aria-live="polite">
              {canvasNodeIds.size}/{nodes.length} düğüm, {canvasEdgeIds.size}/{edges.length} kenar
              {selectedIds.length > 1 && ` · ${selectedIds.length} seçili`}
              {hubGroups.size > 0 && ` · ${hubGroups.size} hub daraltıldı`}
              {multiClusters.length > 0 && ` · ${multiClusters.length} kimlik kümesi`}
              {layoutMs > 0 && ` — yerleşim ${layoutMs} ms`}
            </span>
            <button
              type="button"
              onClick={fitVisible}
              className="btn-secondary btn-sm"
              aria-label="Görünür düğümlere sığdır"
              title="Görünür düğümlere sığdır"
            >
              <Focus size={12} aria-hidden />
              Sığdır
            </button>
            <button type="button" onClick={() => runExport("PNG", exportPng)}
              className="btn-secondary btn-sm"
              aria-label="PNG olarak indir">
              <ImageIcon size={12} aria-hidden />
              PNG
            </button>
            <button type="button" onClick={() => runExport("SVG", exportSvg)}
              className="btn-secondary btn-sm"
              aria-label="SVG olarak indir">
              <Download size={12} aria-hidden />
              SVG
            </button>
            <button type="button" onClick={() => runExport("JSON", exportJson)}
              className="btn-secondary btn-sm"
              aria-label="JSON olarak indir">
              <Download size={12} aria-hidden />
              JSON
            </button>
            <button type="button" onClick={() => runExport("GEXF", exportGexf)}
              className="btn-secondary btn-sm"
              aria-label="GEXF olarak indir">
              <FileCode2 size={12} aria-hidden />
              GEXF
            </button>
          </div>
        </div>


      </section>

      {exportError && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-2 rounded-[6px] border border-danger-line bg-danger-soft px-3 py-2 text-[12.5px] text-danger"
        >
          <span className="inline-flex items-center gap-2" title={exportError.detail}>
            <AlertTriangle size={13} aria-hidden />
            {exportError.message}
          </span>
          <button
            type="button"
            onClick={() => setExportError(null)}
            className="btn-quiet btn-sm"
          >
            <X size={12} aria-hidden />
            Kapat
          </button>
        </div>
      )}

      {/* ---------- Tuval + detay paneli ---------- */}
      <div className={`grid gap-5 ${selectedNode ? "xl:grid-cols-[minmax(0,1fr)_380px]" : ""}`}>
        <div
          ref={canvasBoxRef}
          className="relative h-[70vh] min-h-[560px] overflow-hidden rounded-[8px] border border-line bg-surface"
          onMouseLeave={() => setHover(null)}
        >
          <GraphCanvas
            ref={canvasRef}
            className="h-full w-full"
            data={graphData}
            layout={prefs.layout}
            selectedId={selectedId}
            selectedIds={selectedIds}
            visibleNodeIds={canvasNodeIds}
            visibleEdgeIds={canvasEdgeIds}
            nodeColors={nodeColors}
            edgeColors={EDGE_TYPE_COLORS}
            pathNodeIds={pathNodeIds}
            pathEdgeIds={pathEdgeIds}
            onSelect={handleSelect}
            onSelectionChange={handleSelectionChange}
            onEdgeHover={handleEdgeHover}
            onEdgeClick={handleEdgeClick}
            onContextMenu={handleContextMenu}
            onLayout={handleLayout}
          />

          {contextMenu && (
            <GraphContextMenu
              x={contextMenu.x}
              y={contextMenu.y}
              containerWidth={canvasBoxRef.current?.clientWidth ?? 800}
              containerHeight={canvasBoxRef.current?.clientHeight ?? 600}
              items={menuItems}
              onAction={runContextAction}
              onClose={() => setContextMenu(null)}
            />
          )}

          {/* Canlı bölge: seçim/gizleme eylemleri ekran okuyucuya duyurulur. */}
          <p className="sr-only" role="status" aria-live="polite">
            {graphNotice}
          </p>

          {edges.length === 0 && (
            <p className="pointer-events-none absolute left-3 top-3 flex items-center gap-1.5 rounded-[6px] border border-line bg-surface/95 px-2.5 py-1.5 text-[12px] text-ink-3">
              <Info size={12} className="text-ink-3" aria-hidden />
              Kenar yok, yalnızca kayıtlar gösteriliyor.
            </p>
          )}

          {hover && hoverEdge && (
            <div
              className="pointer-events-none absolute z-20 max-w-[280px] -translate-x-1/2 -translate-y-[calc(100%+10px)] rounded-[6px] border border-line-2 bg-overlay px-3 py-2 shadow-popover"
              style={{ left: hover.x, top: hover.y }}
              role="tooltip"
            >
              <p className="flex items-center gap-1.5 text-[12px] font-medium text-ink">
                <span
                  className="h-2 w-2 shrink-0 rounded-[1px]"
                  style={{ backgroundColor: edgeColor(hoverEdge.type) }}
                  aria-hidden
                />
                {EDGE_TYPE_LABEL(hoverEdge.type)}
                <span className="numeric ml-auto text-ink-2">
                  {Math.round(hoverEdge.confidence * 100)}%
                </span>
              </p>
              {hoverEdge.reason ? (
                <p className="mt-1 text-[12px] leading-snug text-ink-3">{hoverEdge.reason}</p>
              ) : (
                <p className="mt-1 text-[12px] text-ink-3">Gerekçe kaydı yok.</p>
              )}
            </div>
          )}

          <p className="pointer-events-none absolute bottom-3 left-3 max-w-[calc(100%-200px)] text-[12px] text-ink-3">
            {selectedIds.length > 1
              ? `${selectedIds.length} düğüm seçili — Esc ile temizleyin`
              : "Shift-sürükle kutu seçimi · Ctrl/Shift-tık çoklu seçim · sağ tık eylemler"}
          </p>
        </div>

        {selectedNode && (
          <aside
            ref={panelRef}
            role="dialog"
            aria-modal="false"
            aria-labelledby="entity-panel-title"
            tabIndex={-1}
            className="panel flex max-h-[70vh] flex-col overflow-hidden focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/25"
          >
            <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
              <div className="min-w-0">
                <h3 id="entity-panel-title" className="truncate text-[14px] font-semibold text-ink">
                  {selectedNode.label}
                </h3>
                <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="inline-flex items-center gap-1.5 text-[12px] text-ink-3">
                    <span
                      className="h-2 w-2 shrink-0 rounded-[1px]"
                      style={{ backgroundColor: platformColor(selectedNode.platform) }}
                      aria-hidden
                    />
                    {platformMeta(selectedNode.platform).label}
                  </span>
                  <span className="text-[12px] text-ink-3">{selectedNode.type}</span>
                  <span className="numeric text-[13px] font-medium text-ink">
                    {Math.round(selectedNode.score * 100)}%
                  </span>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setSelectedId(null)}
                className="btn-quiet btn-sm shrink-0"
                aria-label="Entity panelini kapat"
                title="Kapat (Esc)"
              >
                <X size={13} aria-hidden />
              </button>
            </div>

            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-3">
              <div className="flex flex-wrap gap-2">
                <Link
                  href={`/cases/${caseId}/findings?entity=${encodeURIComponent(selectedNode.id)}`}
                  className="btn-secondary btn-sm"
                  title={`Bulgular sekmesinde bu entity'yi vurgular (${selectedFindingId ?? ""})`}
                >
                  <ExternalLink size={12} aria-hidden />
                  Bulgular sekmesinde aç
                </Link>
                <button
                  type="button"
                  onClick={focusSelected}
                  className="btn-secondary btn-sm"
                  aria-label="Düğüme odaklan"
                >
                  <Focus size={12} aria-hidden />
                  Odakla
                </button>
                {safeUrl(selectedNode.url) && (
                  <a
                    href={safeUrl(selectedNode.url) as string}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="btn-secondary btn-sm"
                  >
                    <ExternalLink size={12} aria-hidden />
                    Profil
                  </a>
                )}
              </div>

              {selectedNode.clusterSize > 1 && (
                <div className="rounded-[6px] border border-accent-line bg-accent-soft px-3 py-2 text-[12px] text-accent-ink">
                  <p className="flex flex-wrap items-center gap-x-2 gap-y-1 font-medium">
                    <Layers size={12} aria-hidden />
                    Kimlik kümesi · {selectedNode.clusterSize} hesap
                    <span className="numeric">
                      {Math.round(selectedNode.clusterConfidence * 100)}% küme güveni
                    </span>
                  </p>
                  <p className="mt-1 text-ink-3">
                    {selectedNode.clusterPlatforms.join(", ") || "platform bilgisi yok"}
                  </p>
                  {selectedCluster?.reasons[0] && (
                    <p className="mt-1 text-[11.5px] text-ink-3">
                      {selectedCluster.reasons[0]}
                    </p>
                  )}
                  <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
                    <button
                      type="button"
                      onClick={() => updatePrefs({ clusterId: selectedNode.clusterId })}
                      className="text-[12px] font-medium underline decoration-accent-line underline-offset-2 hover:decoration-accent"
                    >
                      Kümeyi grafikte izole et
                    </button>
                    {selectedNode.clusterId && (
                      <Link
                        href={`/cases/${caseId}/findings?cluster=${encodeURIComponent(
                          selectedNode.clusterId
                        )}`}
                        className="text-[12px] font-medium underline decoration-accent-line underline-offset-2 hover:decoration-accent"
                      >
                        Kümeyi bulgularda aç
                      </Link>
                    )}
                  </div>
                </div>
              )}

              {selectedFindingId && (
                <p className="numeric break-all rounded-[6px] border border-line bg-sunken px-2.5 py-1.5 text-[12px] text-ink-3">
                  bulgu kimliği: {selectedFindingId}
                </p>
              )}

              <div>
                <p className="section-heading">Nitelikler</p>
                {Object.keys(selectedNode.attributes).length === 0 ? (
                  <p className="mt-1.5 text-[12px] text-ink-3">Nitelik kaydı yok.</p>
                ) : (
                  <dl className="mt-2 space-y-1.5">
                    {sortedAttributes(selectedNode.attributes)
                      .slice(0, 24)
                      .map(([key, value]) => (
                        <div
                          key={key}
                          className="grid grid-cols-[104px_minmax(0,1fr)] gap-2 text-[12px]"
                        >
                          <dt className="truncate font-mono text-ink-3" title={key}>
                            {key}
                          </dt>
                          <dd className="break-words text-ink-2">{attrText(value)}</dd>
                        </div>
                      ))}
                  </dl>
                )}
              </div>

              <div>
                <p className="section-heading">Bağlantılar ({linkedEdges.length})</p>
                {hiddenLinkedCount > 0 && (
                  <p className="mt-1.5 text-[12px] text-ink-3">
                    {hiddenLinkedCount} bağlantı filtre nedeniyle gizli.
                  </p>
                )}
                {linkedEdges.length === 0 ? (
                  <p className="mt-1.5 text-[12px] text-ink-3">
                    {hiddenLinkedCount > 0 ? "Görünür kenar yok." : "Bu kaydın kenarı yok."}
                  </p>
                ) : (
                  <ul className="mt-2 space-y-2">
                    {linkedEdges.map(({ edge, outgoing, other }) => (
                      <li
                        key={edge.id}
                        className="rounded-[6px] border border-line bg-sunken px-2.5 py-2"
                      >
                        <div className="flex items-center gap-1.5">
                          <span
                            className="h-2 w-2 shrink-0 rounded-[1px]"
                            style={{ backgroundColor: edgeColor(edge.type) }}
                            aria-hidden
                          />
                          <span className="text-[12px] font-medium text-ink-2">
                            {EDGE_TYPE_LABEL(edge.type)}
                          </span>
                          <span className="numeric text-[12px] text-ink-2">
                            {Math.round(edge.confidence * 100)}%
                          </span>
                          <span className="ml-auto text-[12px] text-ink-3">
                            {outgoing ? "giden" : "gelen"}
                          </span>
                        </div>
                        {other && (
                          <button
                            type="button"
                            onClick={() => jumpToNode(other.id)}
                            className="mt-1 block max-w-full truncate text-left text-[12px] text-accent-ink hover:underline"
                            title={`${other.label} — ${other.platform}`}
                          >
                            {other.label}
                          </button>
                        )}
                        {edge.reason && (
                          <p className="mt-1 text-[12px] leading-snug text-ink-3">{edge.reason}</p>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </aside>
        )}
      </div>
      <details className="panel">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-[13px] font-medium text-ink [&::-webkit-details-marker]:hidden">
          <span>Filtreler</span>
          <span className="numeric text-[12px] font-normal text-ink-3">
            {visibleNodes.length}/{nodes.length} düğüm · {visibleEdges.length}/{edges.length} kenar
          </span>
        </summary>
        <div className="space-y-4 px-4 py-4">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          <label className="flex min-w-[280px] flex-1 items-center gap-3 text-[12px] text-ink-3">
            <span className="whitespace-nowrap">
              Güven eşiği{" "}
              <span className="numeric text-ink">{prefs.minConfidence.toFixed(2)}</span>
            </span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={prefs.minConfidence}
              onChange={(e) => updatePrefs({ minConfidence: Number(e.target.value) })}
              aria-label="Kenar güven eşiği"
              className="h-1 w-full max-w-xs cursor-pointer accent-[rgb(var(--accent))]"
            />
            <span className="numeric whitespace-nowrap text-ink-3">
              {hiddenEdges > 0 ? `${hiddenEdges} gizli` : "tümü görünür"}
            </span>
          </label>

          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <span className="text-[12px] text-ink-3">Kenar tipi</span>
            {edgeTypeCounts.length === 0 && (
              <span className="text-[12px] text-ink-3">kenar yok</span>
            )}
            {edgeTypeCounts.map(([type, count]) => {
              const included = prefs.edgeTypes.includes(type);
              const active = prefs.edgeTypes.length === 0 || included;
              return (
                <button
                  key={type}
                  type="button"
                  aria-pressed={included}
                  aria-label={`${EDGE_TYPE_LABEL(type)} kenar tipini ${
                    included ? "filtreden çıkar" : "filtreye ekle"
                  }`}
                  onClick={() => toggleEdgeType(type)}
                  className={`inline-flex items-center gap-1.5 text-[12px] transition-colors ${
                    active ? "text-ink-2" : "text-ink-3 line-through"
                  }`}
                >
                  <span
                    className="h-2 w-2 shrink-0 rounded-[1px]"
                    style={{ backgroundColor: active ? edgeColor(type) : MUTED_DOT }}
                    aria-hidden
                  />
                  {EDGE_TYPE_LABEL(type)}
                  <span className="numeric text-ink-3">{count}</span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-line pt-4">
          <span className="text-[12px] text-ink-3">Platform</span>
          {platformCounts.map(([platform, count]) => {
            const included = prefs.platforms.includes(platform);
            const active = prefs.platforms.length === 0 || included;
            return (
              <button
                key={platform}
                type="button"
                aria-pressed={included}
                aria-label={`${platformMeta(platform).label} platformunu ${
                  included ? "filtreden çıkar" : "filtreye ekle"
                }`}
                onClick={() => togglePlatform(platform)}
                className={`inline-flex items-center gap-1.5 text-[12px] transition-colors ${
                  active ? "text-ink-2" : "text-ink-3 line-through"
                }`}
              >
                <span
                  className="h-2 w-2 shrink-0 rounded-[1px]"
                  style={{
                    backgroundColor: active ? platformColor(platform) : MUTED_DOT,
                  }}
                  aria-hidden
                />
                {platformMeta(platform).label}
                <span className="numeric text-ink-3">{count}</span>
              </button>
            );
          })}
          {(prefs.platforms.length > 0 ||
            prefs.edgeTypes.length > 0 ||
            prefs.minConfidence > 0 ||
            prefs.minScore > 0 ||
            prefs.connectedOnly ||
            prefs.clusterId) && (
            <button
              type="button"
              onClick={resetFilters}
              className="btn-quiet btn-sm"
              aria-label="Filtreleri sıfırla"
            >
              <X size={12} aria-hidden />
              Sıfırla
            </button>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-line pt-4">
          <label className="flex min-w-[240px] flex-1 items-center gap-3 text-[12px] text-ink-3">
            <span className="whitespace-nowrap">
              Düğüm skoru{" "}
              <span className="numeric text-ink">{prefs.minScore.toFixed(2)}</span>
            </span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={prefs.minScore}
              onChange={(e) => updatePrefs({ minScore: Number(e.target.value) })}
              aria-label="Düğüm güven skoru eşiği"
              className="h-1 w-full max-w-xs cursor-pointer accent-[rgb(var(--accent))]"
            />
          </label>
          <button
            type="button"
            aria-pressed={prefs.connectedOnly}
            onClick={() => updatePrefs({ connectedOnly: !prefs.connectedOnly })}
            title="Kenarı olmayan, tek başına duran kayıtları gizle"
            className={`inline-flex items-center gap-1.5 text-[12px] transition-colors ${
              prefs.connectedOnly ? "font-medium text-ink" : "text-ink-3 hover:text-ink"
            }`}
          >
            <Focus size={12} aria-hidden />
            Yalnız bağlı düğümler
          </button>
        </div>
        {multiClusters.length > 0 && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-line pt-4">
            <span className="text-[12px] text-ink-3">Kimlik kümesi</span>
            <button
              type="button"
              aria-pressed={prefs.clusterId === null}
              onClick={() => updatePrefs({ clusterId: null })}
              className={`rounded-[4px] px-1.5 py-0.5 text-[12px] transition-colors ${
                prefs.clusterId === null
                  ? "bg-sunken font-medium text-ink"
                  : "text-ink-3 hover:text-ink"
              }`}
            >
              Tümü
            </button>
            {multiClusters.slice(0, 8).map((cluster) => {
              const active = prefs.clusterId === cluster.id;
              return (
                <button
                  key={cluster.id}
                  type="button"
                  aria-pressed={active}
                  onClick={() =>
                    updatePrefs({ clusterId: active ? null : cluster.id })
                  }
                  title={`${cluster.reasons[0] ?? cluster.platforms.join(", ")} — ${cluster.evidence_count} kanıt`}
                  className={`inline-flex items-center gap-1.5 rounded-[4px] px-1.5 py-0.5 text-[12px] transition-colors ${
                    active
                      ? "bg-accent-soft font-medium text-accent-ink"
                      : "text-ink-3 hover:text-ink"
                  }`}
                >
                  <Layers size={11} aria-hidden />
                  <span className="numeric">{cluster.size}</span> hesap
                  <span className="numeric text-ink-3">
                    {Math.round(cluster.max_confidence * 100)}%
                  </span>
                </button>
              );
            })}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-line pt-4">
          <button
            type="button"
            aria-pressed={prefs.collapseHubs}
            onClick={() => updatePrefs({ collapseHubs: !prefs.collapseHubs })}
            title="Çok yapraklı hub düğümlerinin yapraklarını tek +N düğümünde topla"
            className={`inline-flex items-center gap-1.5 text-[12px] transition-colors ${
              prefs.collapseHubs ? "font-medium text-ink" : "text-ink-3 hover:text-ink"
            }`}
          >
            <Layers size={12} aria-hidden />
            Hub'ları daralt
            {hubGroups.size > 0 && (
              <span className="numeric text-ink-3">{hubGroups.size}</span>
            )}
          </button>
          {expandedHubs.length > 0 && (
            <button
              type="button"
              onClick={() => setExpandedHubs([])}
              className="text-[12px] text-accent-ink underline decoration-accent-line underline-offset-2 hover:decoration-accent"
            >
              Açılan hub'ları yeniden daralt ({expandedHubs.length})
            </button>
          )}
          {(scope.expand.length > 0 || scope.hide.length > 0 || scope.only) && (
            <button
              type="button"
              onClick={() => setScope({ expand: [], hide: [], only: null })}
              className="btn-quiet btn-sm"
              aria-label="Grafik kapsamını sıfırla"
            >
              <X size={12} aria-hidden />
              Kapsamı sıfırla
            </button>
          )}
        </div>
        </div>
      </details>

      <details className="panel">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-[13px] font-medium text-ink [&::-webkit-details-marker]:hidden">
          <span>Yol</span>
          <span className="text-[12px] font-normal text-ink-3">
            {pathNodeIds.length > 0 ? `${pathNodeIds.length - 1} adım vurgulu` : "En kısa bağlantı yolunu bul"}
          </span>
        </summary>
        <div className="space-y-3 px-4 py-4">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <span className="text-[12px] text-ink-3">Bağlantı yolu</span>
          <select
            value={pathFrom}
            onChange={(e) => setPathFrom(e.target.value)}
            aria-label="Yol başlangıç düğümü"
            className="select w-auto max-w-[190px] py-1 text-[12px]"
          >
            <option value="">Başlangıç…</option>
            {visibleNodes.map((node) => (
              <option key={node.id} value={node.id}>
                {node.label} · {platformMeta(node.platform).label}
              </option>
            ))}
          </select>
          <span className="text-ink-3" aria-hidden>
            →
          </span>
          <select
            value={pathTo}
            onChange={(e) => setPathTo(e.target.value)}
            aria-label="Yol hedef düğümü"
            className="select w-auto max-w-[190px] py-1 text-[12px]"
          >
            <option value="">Hedef…</option>
            {visibleNodes.map((node) => (
              <option key={node.id} value={node.id}>
                {node.label} · {platformMeta(node.platform).label}
              </option>
            ))}
          </select>
          <button type="button" onClick={findShortestPath} className="btn-secondary btn-sm">
            Yolu bul
          </button>
          {(pathNodeIds.length > 0 || pathError) && (
            <button type="button" onClick={clearPath} className="btn-quiet btn-sm">
              <X size={12} aria-hidden />
              Temizle
            </button>
          )}
        </div>

        {pathError && (
          <p role="alert" className="rounded-[6px] border border-warn-line bg-warn-soft px-3 py-2 text-[12px] text-warn">
            {pathError}
          </p>
        )}

        {pathNodeIds.length > 0 && (
          <div className="rounded-[6px] border border-accent-line bg-accent-soft px-3 py-2 text-[12px] text-accent-ink">
            <p className="font-medium">
              En kısa yol · {pathNodeIds.length - 1} adım
            </p>
            <div className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-ink-2">
              {pathNodeIds.map((nodeId, index) => {
                const node = nodes.find((item) => item.id === nodeId);
                return (
                  <span key={`${nodeId}-${index}`} className="inline-flex items-center gap-1.5">
                    {index > 0 && (
                      <span className="text-ink-3" aria-hidden>
                        →
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={() => jumpToNode(nodeId)}
                      className="hover:underline"
                    >
                      {node?.label ?? nodeId}
                      {node && (
                        <span className="ml-1 text-[11px] text-ink-3">
                          {platformMeta(node.platform).label}
                        </span>
                      )}
                    </button>
                  </span>
                );
              })}
            </div>
            <p className="mt-1 text-[11.5px] text-ink-3">
              Vurgu grafikte sarı kenarlarla gösterilir.
            </p>
          </div>
        )}
        </div>
      </details>

    </div>
  );
}
