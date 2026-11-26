"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { AlertTriangle, Check, Loader2, Sparkles, Trash2, X } from "lucide-react";
import {
  api,
  type HypothesisRow,
  type PoolRelationRow,
  type ReasoningGraph,
} from "../../../../lib/api";
import { Markdown } from "../../../../components/Markdown";

const RELATION_TR: Record<string, string> = {
  same_person: "aynı kişi",
  owns_account: "hesap sahibi",
  uses_email: "e-posta kullanır",
  linked_from: "bağlantılı",
  located_in: "konum",
  works_at: "çalışır",
  alias_of: "takma ad",
  contradicts: "çelişir",
};

const STATUS_TR: Record<string, string> = {
  proposed: "öneri",
  candidate: "aday",
  confirmed: "onaylı",
  rejected: "reddedildi",
};

function Evidence({ rows }: { rows: { rule: string; note: string; weight: number }[] }) {
  if (!rows.length) return <p className="text-ink-3">Kanıt yok.</p>;
  return (
    <ul className="space-y-0.5">
      {rows.slice(0, 8).map((row, index) => (
        <li key={index} className="text-ink-2">
          <span className="numeric text-ink-3">{row.rule}</span> · {row.note || "—"}{" "}
          <span className="numeric text-ink-3">(+{Number(row.weight).toFixed(1)})</span>
        </li>
      ))}
    </ul>
  );
}

export default function HypothesesPage() {
  const { id } = useParams<{ id: string }>();
  const [hypotheses, setHypotheses] = useState<HypothesisRow[]>([]);
  const [graph, setGraph] = useState<ReasoningGraph | null>(null);
  const [contradictions, setContradictions] = useState<
    { kind: string; item_ids: string[]; message: string }[]
  >([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [h, g] = await Promise.all([api.listHypotheses(id), api.getReasoningGraph(id)]);
      setHypotheses(h.items);
      setGraph(g);
    } catch (e: any) {
      setError(e.message || "Hipotezler yüklenemedi");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const labels = useMemo(() => {
    const map: Record<string, string> = {};
    graph?.nodes.forEach((node) => {
      map[node.id] = `${node.kind}: ${node.value}`.slice(0, 70);
    });
    return map;
  }, [graph]);

  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await fn();
      await load();
    } catch (e: any) {
      setError(e.message || "İşlem başarısız");
    } finally {
      setBusy(false);
    }
  };

  const correlate = () =>
    act(async () => {
      const result = await api.runCorrelation(id);
      setContradictions(result.contradictions);
      setNotice(
        `${result.relations_created} yeni ilişki, ${result.promoted} aday yükseltme, ` +
          `${result.contradictions.length} çelişki.`
      );
    });

  const generate = () =>
    act(async () => {
      const result = await api.generateHypotheses(id);
      setNotice(`${result.total} hipotez güncellendi.`);
    });

  const decideRelation = (rel: PoolRelationRow, status: "confirmed" | "rejected") =>
    act(async () => {
      await api.patchRelation(id, rel.id, { status });
    });

  const pending = (graph?.edges || []).filter(
    (edge) => edge.status === "proposed" || edge.status === "candidate"
  );

  return (
    <div className="space-y-6">
      <div>
        <h2 className="section-heading">Hipotez panosu</h2>
        <p className="page-subtitle">
          Havuz ilişkileri deterministik kurallarla korele edilir; güven puanı kanıtların
          log-odds toplamından hesaplanır (yapay zekâ yalnız gerekçe yazar). Hiçbir hipotez veya
          ilişki siz onaylamadan onaylı sayılmaz.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" className="btn-secondary btn-sm" onClick={correlate} disabled={busy}>
            {busy ? <Loader2 size={12} className="animate-spin" aria-hidden /> : <Check size={12} aria-hidden />}
            Korelasyonu çalıştır
          </button>
          <button type="button" className="btn-primary btn-sm" onClick={generate} disabled={busy}>
            <Sparkles size={12} aria-hidden />
            Hipotez üret
          </button>
        </div>
      </div>

      {error && (
        <div role="alert" className="rounded-[6px] border border-danger-line bg-danger-soft px-3 py-2 text-[12.5px] text-danger">
          {error}
        </div>
      )}
      {notice && (
        <div role="status" className="rounded-[6px] border border-ok-line bg-ok-soft px-3 py-2 text-[12.5px] text-ok">
          {notice}
        </div>
      )}

      {contradictions.length > 0 && (
        <section className="panel">
          <div className="panel-head">
            <h2 className="panel-title flex items-center gap-2">
              <AlertTriangle size={14} aria-hidden /> Çelişkiler
            </h2>
          </div>
          <ul className="space-y-1 p-4 text-[12.5px] text-warn">
            {contradictions.map((item, index) => (
              <li key={index}>{item.message}</li>
            ))}
          </ul>
        </section>
      )}

      {loading ? (
        <p className="text-[13px] text-ink-3">Yükleniyor</p>
      ) : hypotheses.length === 0 ? (
        <p className="text-[13px] text-ink-3">
          Henüz hipotez yok. Önce havuzda öğe/ilişki oluşturun, ardından korelasyonu çalıştırıp
          hipotez üretin.
        </p>
      ) : (
        <section className="panel divide-y divide-line">
          {hypotheses.map((hypothesis) => (
            <article key={hypothesis.id}>
              <div className="panel-head flex-wrap">
                <div className="min-w-0">
                  <p className="text-[13px] font-medium text-ink">{hypothesis.statement}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-3 text-[12px] text-ink-3">
                    <span className="numeric">{STATUS_TR[hypothesis.status] || hypothesis.status}</span>
                    <span className="inline-flex items-center gap-2">
                      <span className="meter w-28" aria-hidden>
                        <span
                          className="block h-full bg-accent"
                          style={{ width: `${Math.round(hypothesis.confidence * 100)}%` }}
                        />
                      </span>
                      <span className="numeric">%{Math.round(hypothesis.confidence * 100)}</span>
                    </span>
                  </div>
                </div>
                <div className="flex shrink-0 flex-wrap gap-1">
                  {(hypothesis.status === "proposed" || hypothesis.status === "candidate") && (
                    <>
                      <button type="button" className="btn-secondary btn-sm" disabled={busy}
                        onClick={() => act(async () => void (await api.patchHypothesis(id, hypothesis.id, { status: "confirmed" })))}>
                        Onayla
                      </button>
                      <button type="button" className="btn-danger-quiet btn-sm" disabled={busy}
                        onClick={() => act(async () => void (await api.patchHypothesis(id, hypothesis.id, { status: "rejected" })))}>
                        Reddet
                      </button>
                    </>
                  )}
                  <button
                    type="button"
                    className="btn-quiet btn-sm max-sm:min-w-[40px]"
                    disabled={busy}
                    aria-label={`${hypothesis.statement.slice(0, 60)} hipotezini sil`}
                    onClick={() => {
                      if (!confirm("Bu hipotez silinsin mi?")) return;
                      void act(async () => void (await api.deleteHypothesis(id, hypothesis.id)));
                    }}
                  >
                    <Trash2 size={12} aria-hidden />
                  </button>
                </div>
              </div>
              <div className="grid gap-4 border-t border-line p-4 text-[12px] sm:grid-cols-2">
                <div>
                  <p className="mb-1 font-medium text-ink-2">Destekleyen kanıtlar</p>
                  <Evidence rows={hypothesis.supporting} />
                </div>
                <div>
                  <p className="mb-1 font-medium text-ink-2">Çelişen kanıtlar</p>
                  <Evidence rows={hypothesis.contradicting} />
                </div>
                {hypothesis.rationale && (
                  <div className="sm:col-span-2">
                    <p className="mb-1 font-medium text-ink-2">Gerekçe</p>
                    <Markdown>{hypothesis.rationale}</Markdown>
                  </div>
                )}
                {hypothesis.next_checks.length > 0 && (
                  <div className="sm:col-span-2">
                    <p className="mb-1 font-medium text-ink-2">Sonraki kontroller</p>
                    <ul className="list-disc space-y-0.5 pl-4 text-ink-2">
                      {hypothesis.next_checks.map((check, index) => (
                        <li key={index}>{check}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </article>
          ))}
        </section>
      )}

      <section className="panel">
        <div className="panel-head">
          <h2 className="panel-title">Onay bekleyen ilişkiler</h2>
          <span className="numeric text-[12px] text-ink-3">{pending.length}</span>
        </div>
        {pending.length === 0 ? (
          <p className="px-4 py-5 text-[12.5px] text-ink-3">Onay bekleyen ilişki yok.</p>
        ) : (
          <ul className="divide-y divide-line">
            {pending.slice(0, 60).map((rel) => (
              <li key={rel.id} className="flex flex-wrap items-center gap-2 px-4 py-2.5 text-[12.5px]">
                <span className="min-w-0 flex-1 basis-full break-words text-ink sm:basis-auto">
                  {labels[rel.src_item_id] || rel.src_item_id}
                  <span className="mx-2 text-ink-3">— {RELATION_TR[rel.type] || rel.type} →</span>
                  {labels[rel.dst_item_id] || rel.dst_item_id}
                </span>
                <span className="numeric text-[12px] text-ink-3">
                  {STATUS_TR[rel.status] || rel.status} · %{Math.round(rel.confidence * 100)}
                </span>
                <button type="button" className="btn-secondary btn-sm" disabled={busy}
                  onClick={() => decideRelation(rel, "confirmed")}>
                  <Check size={12} aria-hidden /> Onayla
                </button>
                <button type="button" className="btn-danger-quiet btn-sm" disabled={busy}
                  onClick={() => decideRelation(rel, "rejected")}>
                  <X size={12} aria-hidden /> Reddet
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
