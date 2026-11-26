"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { AlertTriangle, ExternalLink, Loader2 } from "lucide-react";
import { api, type EvidenceSnapshotRow } from "../../../../lib/api";
import { externalUrl } from "../../../../lib/safe-url";

const SOURCE_TR: Record<string, string> = {
  research: "araştırma",
  subagent: "alt ajan",
  pivot: "pivot",
  citation: "atıf",
};

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString("tr-TR") : "—";
}

export default function EvidencePage() {
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const [items, setItems] = useState<EvidenceSnapshotRow[]>([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<EvidenceSnapshotRow | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const openSeqRef = useRef(0);

  const open = useCallback(
    async (snapshotId: string) => {
      const requestSeq = ++openSeqRef.current;
      try {
        const snapshot = await api.getSnapshot(id, snapshotId);
        if (requestSeq !== openSeqRef.current) return;
        setSelected(snapshot);
        setError(null);
      } catch (e: any) {
        if (requestSeq === openSeqRef.current) {
          setError(e.message || "Kanıt kopyası açılamadı");
        }
      }
    },
    [id]
  );

  const load = useCallback(
    async (q?: string) => {
      setLoading(true);
      try {
        setItems((await api.listSnapshots(id, q)).items);
        setError(null);
      } catch (e: any) {
        setError(e.message || "Kanıt arşivi yüklenemedi");
      } finally {
        setLoading(false);
      }
    },
    [id]
  );

  useEffect(() => {
    void load();
  }, [load]);

  const requested = params.get("snapshot");
  useEffect(() => {
    if (requested) void open(requested);
  }, [requested, open]);
  const selectedUrl = selected ? externalUrl(selected.url) : null;

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
      <section aria-labelledby="evidence-title" className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <h2 id="evidence-title" className="section-heading">
            Kanıt arşivi
          </h2>
          <form
            className="flex w-full gap-2 sm:w-auto"
            onSubmit={(event) => {
              event.preventDefault();
              void load(query.trim() || undefined);
            }}
          >
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="URL veya başlık ara"
              aria-label="Kanıt ara"
              className="input min-w-0 flex-1 sm:w-[200px] sm:flex-none"
            />
            <button type="submit" className="btn-secondary btn-sm shrink-0">
              Ara
            </button>
          </form>
        </div>
        {error && (
          <p role="alert" className="flex items-center gap-1.5 text-[13px] text-danger">
            <AlertTriangle size={14} aria-hidden /> {error}
          </p>
        )}
        {loading ? (
          <p className="flex items-center gap-1.5 text-[13px] text-ink-3">
            <Loader2 size={14} className="animate-spin" aria-hidden /> Yükleniyor…
          </p>
        ) : items.length === 0 ? (
          <p className="panel px-5 py-8 text-[12.5px] text-ink-3">
            Henüz arşivlenmiş sayfa yok. AI araştırma okuduğu sayfaları buraya kaydeder.
          </p>
        ) : (
          <ul className="panel divide-y divide-line">
            {items.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  onClick={() => void open(row.id)}
                  aria-current={selected?.id === row.id ? "true" : undefined}
                  className={`w-full px-4 py-3 text-left transition-colors hover:bg-hover ${
                    selected?.id === row.id ? "bg-accent-soft/40" : ""
                  }`}
                >
                  <p className="break-words text-[13px] font-medium">{row.title || row.url}</p>
                  <p className="break-all text-[12px] text-ink-3">{row.url}</p>
                  <p className="text-[12px] text-ink-3">
                    {SOURCE_TR[row.source] || row.source} · {formatDate(row.fetched_at)}
                    {row.text_truncated ? " · kırpıldı" : ""}
                  </p>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="Kanıt ayrıntısı" className="min-w-0">
        {selected ? (
          <article className="panel space-y-3 p-5">
            <h3 className="break-words text-sm font-semibold">{selected.title || selected.url}</h3>
            {selectedUrl ? (
              <a
                href={selectedUrl}
                target="_blank"
                rel="noreferrer noopener"
                className="inline-flex items-center gap-1 break-all text-[12px] text-accent-ink hover:underline"
              >
                {selected.url}
                <ExternalLink size={11} aria-hidden />
              </a>
            ) : (
              <p className="break-all text-[12px] text-ink-3">
                {selected.url} <span className="text-warn">(güvenli olmayan bağlantı)</span>
              </p>
            )}
            <p
              className={`status ${selected.integrity_ok ? "text-ok" : "text-danger"}`}
            >
              <span
                className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                  selected.integrity_ok ? "bg-ok" : "bg-danger"
                }`}
                aria-hidden
              />
              {selected.integrity_ok ? "Bütünlük doğrulandı" : "Bütünlük doğrulanamadı"}
            </p>
            <dl className="numeric grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[12px] text-ink-3">
              <dt>içerik sha256</dt>
              <dd className="break-all">{selected.sha256}</dd>
              <dt>metin sha256</dt>
              <dd className="break-all">{selected.text_sha256}</dd>
              <dt>alındı</dt>
              <dd>{formatDate(selected.fetched_at)}</dd>
              <dt>saklama sonu</dt>
              <dd>{formatDate(selected.expires_at)}</dd>
            </dl>
            <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-control border border-line bg-sunken p-3 text-[12px]">
              {selected.text}
            </pre>
          </article>
        ) : (
          <p className="text-ink-3">Ayrıntı için soldan bir kayıt seçin.</p>
        )}
      </section>
    </div>
  );
}
