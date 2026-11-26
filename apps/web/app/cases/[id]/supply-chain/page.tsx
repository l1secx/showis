"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Check, ChevronDown, Loader2, Plus, Sparkles, Trash2, X } from "lucide-react";
import {
  api,
  type SupplyChainEdge,
  type SupplyChainGraph,
  type SupplyChainNode,
} from "../../../../lib/api";
import {
  GraphCanvas,
  nodeKindColor,
  type GraphCanvasHandle,
  type GraphData,
} from "../../../../lib/graph-engine";

const NODE_KIND_TR: Record<string, string> = {
  company: "Şirket",
  supplier: "Tedarikçi",
  product: "Ürün",
  person: "Kişi",
  location: "Konum",
  domain: "Domain",
  other: "Diğer",
};

const RELATION_TR: Record<string, string> = {
  supplies: "tedarik eder",
  manufactures: "üretir",
  distributes: "dağıtır",
  subsidiary_of: "bağlı ortaklığı",
  owns: "sahibi",
  partner_of: "ortağı",
  depends_on: "bağımlı",
  located_in: "konumlu",
  related_to: "ilişkili",
};

const STATUS_TR: Record<string, string> = {
  proposed: "onay bekliyor",
  candidate: "aday",
  confirmed: "onaylandı",
  rejected: "reddedildi",
};

const STATUS_TONE: Record<string, string> = {
  proposed: "text-warn",
  candidate: "text-warn",
  confirmed: "text-ok",
  rejected: "text-danger",
};

const STATUS_DOT: Record<string, string> = {
  proposed: "bg-warn",
  candidate: "bg-warn",
  confirmed: "bg-ok",
  rejected: "bg-danger",
};

function Status({ value }: { value: string }) {
  return (
    <span className={`status ${STATUS_TONE[value] || "text-ink-3"}`}>
      <span
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${STATUS_DOT[value] || "bg-ink-4"}`}
        aria-hidden
      />
      {STATUS_TR[value] || value}
    </span>
  );
}

/** Öneri durumundaki kayıt kesikli, reddedilen soluk çizilir. */
function statusPaint(status: string): { dashed?: boolean; faded?: boolean } {
  if (status === "rejected") return { faded: true };
  if (status === "proposed" || status === "candidate") return { dashed: true };
  return {};
}

function SupplyGraph({
  data,
  selectedId,
  onSelect,
  canvasRef,
}: {
  data: SupplyChainGraph;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  canvasRef: RefObject<GraphCanvasHandle | null>;
}) {
  const graph = useMemo<GraphData>(
    () => ({
      nodes: data.nodes.map((node) => ({
        id: node.id,
        label: node.name,
        kind: node.kind,
        weight: 18,
        color: nodeKindColor(node.kind),
        ...statusPaint(node.status),
        meta: { status: node.status },
      })),
      edges: data.edges.map((edge) => ({
        id: edge.id,
        source: edge.source_id,
        target: edge.target_id,
        type: RELATION_TR[edge.relation] || edge.relation,
        weight: 4,
        directed: true,
        ...statusPaint(edge.status),
        meta: { status: edge.status },
      })),
    }),
    [data]
  );

  return (
    <div className="h-[380px] w-full" role="img" aria-label="Tedarik zinciri grafiği">
      <GraphCanvas ref={canvasRef} className="h-full w-full" data={graph} selectedId={selectedId} onSelect={onSelect} />
    </div>
  );
}

export default function SupplyChainPage() {
  const { id } = useParams<{ id: string }>();
  const [data, setData] = useState<SupplyChainGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const canvasRef = useRef<GraphCanvasHandle>(null);

  const [nodeKind, setNodeKind] = useState("company");
  const [nodeName, setNodeName] = useState("");
  const [nodeCountry, setNodeCountry] = useState("");
  const [nodeWebsite, setNodeWebsite] = useState("");

  const [edgeSource, setEdgeSource] = useState("");
  const [edgeTarget, setEdgeTarget] = useState("");
  const [edgeRelation, setEdgeRelation] = useState("supplies");
  const [edgeLabel, setEdgeLabel] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.getSupplyChain(id));
    } catch (e: any) {
      setError(e.message || "Tedarik zinciri yüklenemedi");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const nodes = data?.nodes || [];
  const edges = data?.edges || [];
  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedId) || null,
    [nodes, selectedId]
  );

  /** Seçilen düğüme kamerayı odaklar; grafikte tıklama bu yolu kullanır. */
  const selectNode = useCallback((next: string | null) => {
    setSelectedId(next);
    if (next) canvasRef.current?.focus(next, 1.15);
  }, []);

  const addNode = async (e: React.FormEvent) => {
    e.preventDefault();
    if (nodeName.trim().length < 2) return;
    setBusy(true);
    setError(null);
    try {
      await api.addSupplyChainNode(id, {
        kind: nodeKind,
        name: nodeName.trim(),
        country: nodeCountry.trim() || null,
        website: nodeWebsite.trim() || null,
      });
      setNodeName("");
      setNodeCountry("");
      setNodeWebsite("");
      await load();
    } catch (err: any) {
      setError(err.message || "Düğüm eklenemedi");
    } finally {
      setBusy(false);
    }
  };

  const addEdge = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!edgeSource || !edgeTarget) return;
    setBusy(true);
    setError(null);
    try {
      await api.addSupplyChainEdge(id, {
        source_id: edgeSource,
        target_id: edgeTarget,
        relation: edgeRelation,
        label: edgeLabel.trim() || null,
      });
      setEdgeLabel("");
      await load();
    } catch (err: any) {
      setError(err.message || "İlişki eklenemedi");
    } finally {
      setBusy(false);
    }
  };

  const setNodeStatus = async (node: SupplyChainNode, status: string) => {
    try {
      await api.updateSupplyChainNode(id, node.id, { status });
      await load();
    } catch (e: any) {
      setError(e.message || "Düğüm güncellenemedi");
    }
  };

  const setEdgeStatus = async (edge: SupplyChainEdge, status: string) => {
    try {
      await api.updateSupplyChainEdge(id, edge.id, { status });
      await load();
    } catch (e: any) {
      setError(e.message || "İlişki güncellenemedi");
    }
  };

  const removeNode = async (node: SupplyChainNode) => {
    if (!confirm(`"${node.name}" ve bağlı ilişkiler silinsin mi?`)) return;
    try {
      await api.deleteSupplyChainNode(id, node.id);
      if (selectedId === node.id) setSelectedId(null);
      await load();
    } catch (e: any) {
      setError(e.message || "Düğüm silinemedi");
    }
  };

  const removeEdge = async (edge: SupplyChainEdge) => {
    if (!confirm("İlişki silinsin mi?")) return;
    try {
      await api.deleteSupplyChainEdge(id, edge.id);
      await load();
    } catch (e: any) {
      setError(e.message || "İlişki silinemedi");
    }
  };

  const nodeNameById = useMemo(
    () => Object.fromEntries(nodes.map((node) => [node.id, node.name])),
    [nodes]
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="section-heading">Tedarik zinciri</h2>
          <p className="page-subtitle">
            Şirket, tedarikçi, ürün ve ilişkileri ayrı bir grafikte toplar. AI
            araştırması bulduğu düğümleri ve ilişkileri buraya öneri olarak yazar;
            onay sizde.
          </p>
        </div>
        <Link href={`/cases/${id}/research`} className="btn-primary btn-sm shrink-0">
          <Sparkles size={13} aria-hidden />
          AI ile araştır
        </Link>
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-[6px] border border-danger-line bg-danger-soft px-3 py-2 text-[12.5px] text-danger"
        >
          {error}
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-6">
          <section className="panel">
            <div className="panel-head flex-wrap">
              <div>
                <h2 className="panel-title">İlişki grafiği</h2>
                <p className="mt-0.5 text-[12px] text-ink-3">
                  {nodes.length} düğüm · {edges.length} ilişki
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-ink-3">
                <span>Türler:</span>
                {Object.entries(NODE_KIND_TR).map(([kind, label]) => (
                  <span key={kind} className="inline-flex items-center gap-1.5">
                    <span
                      className="h-2 w-2 shrink-0 rounded-full"
                      style={{ backgroundColor: nodeKindColor(kind) }}
                      aria-hidden
                    />
                    {label}
                  </span>
                ))}
                <span className="text-ink-3">
                  · kesikli: onay bekliyor · soluk: reddedildi
                </span>
              </div>
            </div>
            <div className="border-t border-line">
              {loading ? (
                <p className="flex items-center gap-2 px-5 py-12 text-[13px] text-ink-3">
                  <Loader2 size={14} className="animate-spin" aria-hidden />
                  Grafik yükleniyor
                </p>
              ) : nodes.length === 0 ? (
                <div className="px-5 py-12 text-center">
                  <p className="text-[13px] font-medium text-ink">Henüz düğüm yok</p>
                  <p className="mt-1 text-[12.5px] text-ink-3">
                    Sağdaki formdan ekleyin veya AI araştırmasında tedarik zinciri
                    sorusu sorun.
                  </p>
                </div>
              ) : (
                <SupplyGraph
                  data={data!}
                  selectedId={selectedId}
                  onSelect={selectNode}
                  canvasRef={canvasRef}
                />
              )}
            </div>
            {selectedNode && (
              <div className="border-t border-line px-5 py-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="text-[13px] font-medium text-ink">{selectedNode.name}</p>
                    <p className="mt-0.5 text-[12px] text-ink-3">
                      {NODE_KIND_TR[selectedNode.kind] || selectedNode.kind}
                      {selectedNode.country ? ` · ${selectedNode.country}` : ""}
                      {selectedNode.website ? ` · ${selectedNode.website}` : ""}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Status value={selectedNode.status} />
                    <button
                      type="button"
                      className="btn-quiet btn-sm"
                      onClick={() => setSelectedId(null)}
                    >
                      Kapat
                    </button>
                  </div>
                </div>
              </div>
            )}
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2 className="panel-title">Düğümler</h2>
            </div>
            {nodes.length === 0 ? (
              <p className="px-5 py-6 text-[12.5px] text-ink-3">Kayıt yok.</p>
            ) : (
              <div className="scroll-x border-t border-line">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Ad</th>
                      <th>Tür</th>
                      <th>Ülke</th>
                      <th>Durum</th>
                      <th className="text-right">İşlem</th>
                    </tr>
                  </thead>
                  <tbody>
                    {nodes.map((node) => (
                      <tr key={node.id}>
                        <td className="font-medium text-ink">{node.name}</td>
                        <td className="text-ink-3">
                          {NODE_KIND_TR[node.kind] || node.kind}
                        </td>
                        <td className="text-ink-3">{node.country || "—"}</td>
                        <td>
                          <Status value={node.status} />
                        </td>
                        <td>
                          <div className="flex justify-end gap-1">
                            {(node.status === "proposed" || node.status === "candidate") && (
                              <>
                                <button
                                  type="button"
                                  className="btn-quiet btn-sm"
                                  onClick={() => setNodeStatus(node, "confirmed")}
                                >
                                  <Check size={12} aria-hidden />
                                  Onayla
                                </button>
                                <button
                                  type="button"
                                  className="btn-danger-quiet btn-sm"
                                  onClick={() => setNodeStatus(node, "rejected")}
                                  aria-label={`${node.name} düğümünü reddet`}
                                  title="Reddet"
                                >
                                  <X size={12} aria-hidden />
                                </button>
                              </>
                            )}
                            <button
                              type="button"
                              className="btn-danger-quiet btn-sm"
                              onClick={() => removeNode(node)}
                              aria-label={`${node.name} düğümünü sil`}
                              title="Sil"
                            >
                              <Trash2 size={12} aria-hidden />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2 className="panel-title">İlişkiler</h2>
            </div>
            {edges.length === 0 ? (
              <p className="px-5 py-6 text-[12.5px] text-ink-3">İlişki yok.</p>
            ) : (
              <div className="scroll-x border-t border-line">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Kaynak</th>
                      <th>İlişki</th>
                      <th>Hedef</th>
                      <th>Durum</th>
                      <th className="text-right">İşlem</th>
                    </tr>
                  </thead>
                  <tbody>
                    {edges.map((edge) => (
                      <tr key={edge.id}>
                        <td className="text-ink-2">
                          {nodeNameById[edge.source_id] || edge.source_id}
                        </td>
                        <td className="text-ink-3">
                          {edge.label || RELATION_TR[edge.relation] || edge.relation}
                        </td>
                        <td className="text-ink-2">
                          {nodeNameById[edge.target_id] || edge.target_id}
                        </td>
                        <td>
                          <Status value={edge.status} />
                        </td>
                        <td>
                          <div className="flex justify-end gap-1">
                            {(edge.status === "proposed" || edge.status === "candidate") && (
                              <>
                                <button
                                  type="button"
                                  className="btn-quiet btn-sm"
                                  onClick={() => setEdgeStatus(edge, "confirmed")}
                                >
                                  Onayla
                                </button>
                                <button
                                  type="button"
                                  className="btn-danger-quiet btn-sm"
                                  onClick={() => setEdgeStatus(edge, "rejected")}
                                >
                                  Reddet
                                </button>
                              </>
                            )}
                            <button
                              type="button"
                              className="btn-danger-quiet btn-sm"
                              onClick={() => removeEdge(edge)}
                              aria-label={`${nodeNameById[edge.source_id] ?? edge.source_id} → ${
                                nodeNameById[edge.target_id] ?? edge.target_id
                              } ilişkisini sil`}
                              title="Sil"
                            >
                              <Trash2 size={12} aria-hidden />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>

        <aside className="space-y-4">
          <details className="panel group" open>
            <summary className="panel-head cursor-pointer list-none marker:content-none">
              <span className="panel-title inline-flex items-center gap-2">
                <ChevronDown
                  size={14}
                  aria-hidden
                  className="text-ink-3 transition-transform group-open:rotate-180"
                />
                Düğüm ekle
              </span>
            </summary>
            <form onSubmit={addNode} className="space-y-3 border-t border-line p-4">
              <label className="block">
                <span className="label">Tür</span>
                <select className="select" value={nodeKind} onChange={(e) => setNodeKind(e.target.value)}>
                  {Object.entries(NODE_KIND_TR).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="label">Ad</span>
                <input
                  className="input"
                  placeholder="Acme Lojistik A.Ş."
                  value={nodeName}
                  onChange={(e) => setNodeName(e.target.value)}
                  required
                />
              </label>
              <label className="block">
                <span className="label">Ülke</span>
                <input
                  className="input"
                  placeholder="Türkiye"
                  value={nodeCountry}
                  onChange={(e) => setNodeCountry(e.target.value)}
                />
              </label>
              <label className="block">
                <span className="label">Web sitesi</span>
                <input
                  className="input"
                  placeholder="https://example.com"
                  value={nodeWebsite}
                  onChange={(e) => setNodeWebsite(e.target.value)}
                />
              </label>
              <button type="submit" disabled={busy} className="btn-primary btn-sm w-full">
                {busy ? <Loader2 size={13} className="animate-spin" aria-hidden /> : <Plus size={13} aria-hidden />}
                Ekle
              </button>
            </form>
          </details>

          <details className="panel group">
            <summary className="panel-head cursor-pointer list-none marker:content-none">
              <span className="panel-title inline-flex items-center gap-2">
                <ChevronDown
                  size={14}
                  aria-hidden
                  className="text-ink-3 transition-transform group-open:rotate-180"
                />
                İlişki ekle
              </span>
            </summary>
            <form onSubmit={addEdge} className="space-y-3 border-t border-line p-4">
              <label className="block">
                <span className="label">Kaynak</span>
                <select
                  className="select"
                  value={edgeSource}
                  onChange={(e) => setEdgeSource(e.target.value)}
                  required
                >
                  <option value="">Seçin</option>
                  {nodes.map((node) => (
                    <option key={node.id} value={node.id}>
                      {node.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="label">İlişki</span>
                <select
                  className="select"
                  value={edgeRelation}
                  onChange={(e) => setEdgeRelation(e.target.value)}
                >
                  {Object.entries(RELATION_TR).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="label">Hedef</span>
                <select
                  className="select"
                  value={edgeTarget}
                  onChange={(e) => setEdgeTarget(e.target.value)}
                  required
                >
                  <option value="">Seçin</option>
                  {nodes.map((node) => (
                    <option key={node.id} value={node.id}>
                      {node.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="label">Etiket (opsiyonel)</span>
                <input
                  className="input"
                  value={edgeLabel}
                  onChange={(e) => setEdgeLabel(e.target.value)}
                  placeholder="ör. 2024 sözleşmesi"
                />
              </label>
              <button
                type="submit"
                disabled={busy || !edgeSource || !edgeTarget || edgeSource === edgeTarget}
                className="btn-primary btn-sm w-full"
              >
                {busy ? <Loader2 size={13} className="animate-spin" aria-hidden /> : <Plus size={13} aria-hidden />}
                Ekle
              </button>
            </form>
          </details>

          <p className="px-1 text-[11.5px] leading-relaxed text-ink-3">
            Tedarik zinciri grafiği kişi kimlik grafiğinden ayrıdır. AI kayıtları
            onaylanana kadar öneri durumundadır.
          </p>
        </aside>
      </div>
    </div>
  );
}
