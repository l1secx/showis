"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import cytoscape from "cytoscape";
import {
  AlertTriangle,
  Download,
  ExternalLink,
  FileCode2,
  Focus,
  Image as ImageIcon,
  Info,
  RotateCcw,
  X,
} from "lucide-react";
import { api, type GraphData } from "../../../lib/api";
import { platformMeta } from "./platform-meta";
import { readTheme, useTheme, type Theme } from "../../components/use-theme";
import { edgeTypeLabel } from "./vocabulary";

/* ------------------------------------------------------------------ */
/* Sabitler                                                            */
/* ------------------------------------------------------------------ */

/** `#rrggbb` → `rgba(...)`. Açık zeminde marka rengini dolgu yerine kenar olarak kullanırız. */
function tint(hex: string, alpha: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

/** Platform marka rengi — açık temada koyu ton, koyu temada açık ton. */
function platformColor(platform: string, theme: Theme): string {
  const meta = platformMeta(platform);
  return theme === "dark" ? meta.accent : meta.color;
}

/**
 * Cytoscape stil sayfası — temaya göre üretilir.
 *
 * Düğümlerde marka rengi kenar olarak kullanılır, dolgu markanın düşük
 * opaklıklı tonudur; böylece açık zeminde ağır lekeler oluşmaz.
 */
function buildStyle(theme: Theme): cytoscape.StylesheetStyle[] {
  const dark = theme === "dark";
  const textColor = dark ? "#edeff2" : "#15181d";
  const halo = dark ? "#22262c" : "#ffffff";
  const edgeText = dark ? "#8d96a2" : "#6e7784";
  const selectColor = dark ? "#84aad1" : "#1b3a5b";

  return [
    {
      selector: "node",
      style: {
        "background-color": (ele: cytoscape.NodeSingular) =>
          tint(platformColor(String(ele.data("platform")), theme), dark ? 0.2 : 0.16),
        "border-width": 2,
        "border-color": (ele: cytoscape.NodeSingular) =>
          platformColor(String(ele.data("platform")), theme),
        width: "mapData(score, 0, 1, 20, 44)",
        height: "mapData(score, 0, 1, 20, 44)",
        label: "data(label)",
        color: textColor,
        "font-size": 9,
        "min-zoomed-font-size": 7,
        "text-valign": "bottom",
        "text-margin-y": 4,
        "text-wrap": "wrap",
        "text-max-width": "120px",
        "text-outline-color": halo,
        "text-outline-width": 3,
      },
    },
    {
      selector: "node:selected",
      style: {
        "border-width": 4,
        "border-color": selectColor,
        "background-opacity": 1,
      },
    },
    {
      selector: "edge",
      style: {
        width: "mapData(confidence, 0, 1, 0.6, 3.5)",
        "line-color": (ele: cytoscape.EdgeSingular) =>
          EDGE_TYPE_COLORS[theme][String(ele.data("type"))] ?? edgeText,
        "target-arrow-color": (ele: cytoscape.EdgeSingular) =>
          EDGE_TYPE_COLORS[theme][String(ele.data("type"))] ?? edgeText,
        "target-arrow-shape": "triangle",
        "arrow-scale": 0.7,
        "curve-style": "bezier",
        opacity: (ele: cytoscape.EdgeSingular) =>
          0.4 + Math.min(1, Number(ele.data("confidence") ?? 0)) * 0.5,
        label: "",
        "font-size": 7,
        color: edgeText,
        "text-outline-color": halo,
        "text-outline-width": 3,
      },
    },
    {
      selector: "edge.labelled",
      style: {
        label: "data(label)",
        "text-rotation": "autorotate",
      },
    },
  ];
}

/** Dışa aktarılan görselin arka planı — ekrandaki yüzeyin rengi. */
const EXPORT_BG: Record<Theme, string> = {
  light: "#ffffff",
  dark: "#22262c",
};

/**
 * Kenar tipleri. Renkleri temaya göre değişir: koyu zeminde koyu renkler
 * kaybolduğu için açık tonları kullanılır.
 */
const EDGE_TYPE_COLORS: Record<Theme, Record<string, string>> = {
  light: {
    same_person: "#2a5b3f",
    cross_link: "#1b3a5b",
    has_account: "#6e7784",
    mentions: "#8a5a14",
    owned_domain: "#5b3e7a",
  },
  dark: {
    same_person: "#6fbf8e",
    cross_link: "#84aad1",
    has_account: "#8d96a2",
    mentions: "#d9a441",
    owned_domain: "#b79ce0",
  },
};

const EDGE_TYPE_LABEL = edgeTypeLabel;

const LAYOUTS: { value: LayoutName; label: string; hint: string }[] = [
  { value: "cose", label: "Force", hint: "Kuvvet tabanlı (cose) yerleşim" },
  { value: "circle", label: "Circle", hint: "Dairesel yerleşim" },
  { value: "grid", label: "Grid", hint: "Izgara yerleşim" },
];

/* ------------------------------------------------------------------ */
/* Tipler                                                              */
/* ------------------------------------------------------------------ */

type LayoutName = "cose" | "circle" | "grid";

interface GNode {
  id: string;
  label: string;
  type: string;
  platform: string;
  url: string | null;
  score: number;
  attributes: Record<string, unknown>;
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
  platforms: string[];
  edgeTypes: string[];
  layout: LayoutName;
}

interface HoverEdge {
  x: number;
  y: number;
  edge: GEdge;
}

const DEFAULT_PREFS: GraphPrefs = {
  minConfidence: 0,
  platforms: [],
  edgeTypes: [],
  layout: "cose",
};

const prefsKey = (caseId: string) => `showis:graph:${caseId}`;

function readPrefs(caseId: string): GraphPrefs | null {
  if (typeof window === "undefined") return null;
  const raw = window.sessionStorage.getItem(prefsKey(caseId));
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<GraphPrefs>;
    const layout: LayoutName =
      parsed.layout === "circle" || parsed.layout === "grid" || parsed.layout === "cose"
        ? parsed.layout
        : DEFAULT_PREFS.layout;
    const min = Number(parsed.minConfidence);
    return {
      minConfidence: Number.isFinite(min) ? Math.min(1, Math.max(0, min)) : 0,
      platforms: Array.isArray(parsed.platforms) ? parsed.platforms.map(String) : [],
      edgeTypes: Array.isArray(parsed.edgeTypes) ? parsed.edgeTypes.map(String) : [],
      layout,
    };
  } catch {
    return null;
  }
}

function writePrefs(caseId: string, prefs: GraphPrefs) {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(prefsKey(caseId), JSON.stringify(prefs));
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
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const lastLayoutRef = useRef<LayoutName | null>(null);
  const prefsReadyRef = useRef(false);
  const panelRef = useRef<HTMLElement>(null);

  const [error, setError] = useState<string | null>(null);
  const [nodes, setNodes] = useState<GNode[]>([]);
  const [edges, setEdges] = useState<GEdge[]>([]);
  const [loading, setLoading] = useState(true);
  const [prefs, setPrefs] = useState<GraphPrefs>(DEFAULT_PREFS);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hover, setHover] = useState<HoverEdge | null>(null);
  const [stats, setStats] = useState({ visibleNodes: 0, visibleEdges: 0, layoutMs: 0 });
  /** Dışa aktarma hatası — boşsa hata yok. */
  const [exportError, setExportError] = useState<{ message: string; detail?: string } | null>(
    null
  );
  const theme = useTheme();

  /** Kenar rengi — tema ve tipe göre. */
  const edgeColor = (type: string) =>
    EDGE_TYPE_COLORS[theme][type] ?? (theme === "dark" ? "#8d96a2" : "#6e7784");

  /** Filtrelenmiş öğelerin noktası — tema ile sönümlenir. */
  const mutedDot = theme === "dark" ? "#4e5660" : "#c8ced6";

  /* --- tema değişince grafiği yeniden boyamak (yerleşim korunur) --- */
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.style(buildStyle(theme));
  }, [theme]);

  /* --- tercihleri yükle (yalnız istemci) --- */
  useEffect(() => {
    const stored = readPrefs(caseId);
    if (stored) setPrefs(stored);
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
        }));
        const gEdges: GEdge[] = (payload.edges ?? []).map((e) => ({
          id: String(e.data.id),
          source: String(e.data.source),
          target: String(e.data.target),
          type: String(e.data.type ?? e.data.label ?? "unknown"),
          confidence: Number(e.data.confidence ?? 0),
          reason: String(e.data.reason ?? ""),
        }));
        setNodes(gNodes);
        setEdges(gEdges);
      })
      .catch((e: Error) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [caseId]);

  /* --- filtre yardımcıları --- */
  const visibleNodeIds = useMemo(() => {
    const platformSet = new Set(prefs.platforms);
    return new Set(
      nodes.filter((n) => platformSet.size === 0 || platformSet.has(n.platform)).map((n) => n.id)
    );
  }, [nodes, prefs.platforms]);

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

  /* --- cytoscape örneği --- */
  const runLayout = useCallback((name: LayoutName) => {
    const cy = cyRef.current;
    if (!cy) return;
    const total = cy.nodes().length;
    const options =
      name === "cose"
        ? {
            name: "cose",
            animate: total <= 150,
            animationDuration: 400,
            randomize: true,
            fit: true,
            padding: 40,
            nodeRepulsion: total > 300 ? 5000 : 9000,
            idealEdgeLength: total > 300 ? 60 : 90,
            numIter: total > 300 ? 250 : 1000,
          }
        : {
            name,
            animate: false,
            fit: true,
            padding: 40,
            ...(name === "grid" ? { avoidOverlap: true, condense: true } : {}),
          };
    const startedAt = performance.now();
    cy.one("layoutstop", () => {
      setStats((s) => ({ ...s, layoutMs: Math.round(performance.now() - startedAt) }));
    });
    cy.layout(options as cytoscape.LayoutOptions).run();
  }, []);

  useEffect(() => {
    if (!nodes.length || !containerRef.current) return;

    const elements: cytoscape.ElementDefinition[] = [
      ...nodes.map((n) => ({
        data: {
          id: n.id,
          label: n.label,
          platform: n.platform,
          type: n.type,
          score: n.score,
        },
      })),
      ...edges.map((e) => ({
        data: {
          id: e.id,
          source: e.source,
          target: e.target,
          type: e.type,
          confidence: e.confidence,
          label: `${EDGE_TYPE_LABEL(e.type)} ${Math.round(e.confidence * 100)}%`,
        },
      })),
    ];

    const dense = nodes.length > 300;
    const cy = cytoscape({
      container: containerRef.current,
      elements,
      // `wheelSensitivity` bilinçli olarak ayarlanmaz: kütüphane, özel değerin
      // standart farelerde doğal olmayan (ani) yakınlaştırma yaptığını uyarır.
      minZoom: 0.08,
      maxZoom: 3,
      motionBlur: false,
      textureOnViewport: false,
      hideEdgesOnViewport: dense,
      pixelRatio: dense ? 1 : "auto",
      style: buildStyle(readTheme()),
      layout: { name: "preset" },
    });

    cyRef.current = cy;
    lastLayoutRef.current = prefs.layout;
    runLayout(prefs.layout);

    cy.on("tap", "node", (evt) => setSelectedId(evt.target.id()));
    cy.on("tap", (evt) => {
      if (evt.target === cy) setSelectedId(null);
    });
    cy.on("mouseover", "edge", (evt) => {
      const edge = evt.target as cytoscape.EdgeSingular;
      edge.addClass("labelled");
      const position = (evt as unknown as { renderedPosition?: { x: number; y: number } })
        .renderedPosition;
      const found = edges.find((e) => e.id === edge.id());
      if (!found || !position) return;
      setHover({ x: position.x, y: position.y, edge: found });
    });
    cy.on("mouseout", "edge", (evt) => {
      (evt.target as cytoscape.EdgeSingular).removeClass("labelled");
      setHover(null);
    });
    cy.on("grab", () => setHover(null));

    return () => {
      cy.destroy();
      cyRef.current = null;
      lastLayoutRef.current = null;
    };
    // `edges` yalnızca veri değişiminde eleman üretmek için kullanılıyor.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, runLayout]);

  /* --- layout değişimi --- */
  useEffect(() => {
    if (!cyRef.current) return;
    if (lastLayoutRef.current === prefs.layout) return;
    lastLayoutRef.current = prefs.layout;
    runLayout(prefs.layout);
  }, [prefs.layout, runLayout]);

  /* --- filtreleri uygula --- */
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const platformSet = new Set(prefs.platforms);
    const typeSet = new Set(prefs.edgeTypes);
    const keepNodes = new Set<string>();
    let keptEdges = 0;

    cy.batch(() => {
      cy.nodes().forEach((n) => {
        const ok = platformSet.size === 0 || platformSet.has(String(n.data("platform")));
        n.style("display", ok ? "element" : "none");
        if (ok) keepNodes.add(n.id());
      });
      cy.edges().forEach((e) => {
        const type = String(e.data("type"));
        const confidence = Number(e.data("confidence") ?? 0);
        const ok =
          (typeSet.size === 0 || typeSet.has(type)) &&
          confidence >= prefs.minConfidence &&
          keepNodes.has(String(e.data("source"))) &&
          keepNodes.has(String(e.data("target")));
        e.style("display", ok ? "element" : "none");
        if (ok) keptEdges += 1;
      });
    });

    // Kenar etiketleri dinlenme anında görünür: dinlenme hâlinde düğüm
    // yazılarıyla çakışıyor ve grafiğin okunmasını zorlaştırıyordu.
    cy.edges().removeClass("labelled");
    setStats((s) => ({ ...s, visibleNodes: keepNodes.size, visibleEdges: keptEdges }));
  }, [prefs.minConfidence, prefs.platforms, prefs.edgeTypes, nodes, edges]);

  /* --- seçimi cytoscape'e yansıt --- */
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().unselect();
    cy.edges().removeClass("labelled");
    if (selectedId) {
      const node = cy.$id(selectedId);
      if (node.nonempty()) {
        node.select();
        // Seçili kaydın bağlantılarının güven değerleri etiketlenir.
        node.connectedEdges().addClass("labelled");
      }
    }
  }, [selectedId, nodes]);

  /* --- Esc ile paneli kapat, odağı panele ver --- */
  useEffect(() => {
    if (!selectedId) return;
    panelRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelectedId(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedId]);

  /* --- türetilmiş seçim verisi --- */
  const selectedNode = useMemo(
    () => nodes.find((n) => n.id === selectedId) ?? null,
    [nodes, selectedId]
  );

  const visibleEdgeIds = useMemo(() => new Set(visibleEdges.map((e) => e.id)), [visibleEdges]);

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

  /**
   * Dışa aktarım hatası — sessizce yutulmaz. Görsel, JSON ve GEXF üretimi
   * tarayıcıda çalışır; başarısızlıkta kullanıcıya söylenmelidir.
   */
  const runExport = (kind: "PNG" | "JSON" | "GEXF", fn: () => void) => {
    setExportError(null);
    try {
      fn();
    } catch (e: any) {
      setExportError({
        message: `${kind} dışa aktarımı başarısız oldu. Tarayıcıyı ve ağ bağlantısını kontrol edip yeniden deneyin.`,
        detail: e?.message,
      });
    }
  };

  const exportPng = () => {
    const cy = cyRef.current;
    if (!cy) return;
    const dense = nodes.length > 300;
    // Arka plan, ekrandaki yüzeyle aynı olmalı — eski koyu temanın siyahı değil.
    const uri = cy.png({ full: true, scale: dense ? 1 : 2, bg: EXPORT_BG[theme] });
    downloadHref(`${exportBase}.png`, uri);
  };

  const filteredPayload = () => {
    const nodeIds = new Set(visibleNodeIds);
    return {
      exported_at: new Date().toISOString(),
      case_id: caseId,
      filters: prefs,
      nodes: nodes.filter((n) => nodeIds.has(n.id)),
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
    const cy = cyRef.current;
    if (!cy || !selectedId) return;
    const node = cy.$id(selectedId);
    if (node.empty()) return;
    cy.animate({ center: { eles: node }, zoom: Math.max(cy.zoom(), 1.2) }, { duration: 300 });
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

  const resetFilters = () =>
    updatePrefs({ minConfidence: 0, platforms: [], edgeTypes: [] });

  const hiddenEdges = edges.length - visibleEdges.length;

  return (
    <div className="space-y-5">
      {/* ---------- Araç çubuğu ---------- */}
      <section className="panel space-y-4 p-4">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
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
              onClick={() => runLayout(prefs.layout)}
              className="btn-quiet btn-sm"
              aria-label="Grafiği yeniden yerleştir"
              title="Yeniden yerleştir"
            >
              <RotateCcw size={12} aria-hidden />
            </button>
          </div>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            <span className="numeric text-ink-3" aria-live="polite">
              {stats.visibleNodes}/{nodes.length} düğüm, {stats.visibleEdges}/{edges.length} kenar
              {stats.layoutMs > 0 && ` — yerleşim ${stats.layoutMs} ms`}
            </span>
            <button type="button" onClick={() => runExport("PNG", exportPng)}
              className="btn-secondary btn-sm"
              aria-label="PNG olarak indir">
              <ImageIcon size={12} aria-hidden />
              PNG
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

        <div className="flex flex-wrap items-center gap-x-6 gap-y-3 border-t border-line pt-4">
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
                    style={{ backgroundColor: active ? edgeColor(type) : mutedDot }}
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
                    backgroundColor: active ? platformColor(platform, theme) : mutedDot,
                  }}
                  aria-hidden
                />
                {platformMeta(platform).label}
                <span className="numeric text-ink-3">{count}</span>
              </button>
            );
          })}
          {(prefs.platforms.length > 0 || prefs.edgeTypes.length > 0 || prefs.minConfidence > 0) && (
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
          className="relative overflow-hidden rounded-[8px] border border-line bg-surface"
          onMouseLeave={() => setHover(null)}
        >
          <div ref={containerRef} className="h-[560px] w-full xl:h-[620px]" />

          {edges.length === 0 && (
            <p className="pointer-events-none absolute left-3 top-3 flex items-center gap-1.5 rounded-[6px] border border-line bg-surface/95 px-2.5 py-1.5 text-[12px] text-ink-3">
              <Info size={12} className="text-ink-3" aria-hidden />
              Kenar yok, yalnızca kayıtlar gösteriliyor.
            </p>
          )}

          {hover && (
            <div
              className="pointer-events-none absolute z-20 max-w-[280px] -translate-x-1/2 -translate-y-[calc(100%+10px)] rounded-[6px] border border-line-2 bg-overlay px-3 py-2 shadow-popover"
              style={{ left: hover.x, top: hover.y }}
              role="tooltip"
            >
              <p className="flex items-center gap-1.5 text-[12px] font-medium text-ink">
                <span
                  className="h-2 w-2 shrink-0 rounded-[1px]"
                  style={{ backgroundColor: edgeColor(hover.edge.type) }}
                  aria-hidden
                />
                {EDGE_TYPE_LABEL(hover.edge.type)}
                <span className="numeric ml-auto text-ink-2">
                  {Math.round(hover.edge.confidence * 100)}%
                </span>
              </p>
              {hover.edge.reason ? (
                <p className="mt-1 text-[12px] leading-snug text-ink-3">{hover.edge.reason}</p>
              ) : (
                <p className="mt-1 text-[12px] text-ink-3">Gerekçe kaydı yok.</p>
              )}
            </div>
          )}

          {selectedNode && (
            <p className="pointer-events-none absolute bottom-3 left-3 text-[12px] text-ink-3">
              Bir düğüme tıklayın — Esc ile paneli kapatın
            </p>
          )}
        </div>

        {selectedNode && (
          <aside
            ref={panelRef}
            role="dialog"
            aria-modal="false"
            aria-labelledby="entity-panel-title"
            tabIndex={-1}
            className="panel flex max-h-[620px] flex-col overflow-hidden focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/25"
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
                      style={{ backgroundColor: platformColor(selectedNode.platform, theme) }}
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
                            onClick={() => setSelectedId(other.id)}
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
    </div>
  );
}
