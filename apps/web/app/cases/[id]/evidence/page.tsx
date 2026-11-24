"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { AlertTriangle, ExternalLink, Loader2, ShieldCheck, ShieldAlert } from "lucide-react";
import { api, type EvidenceSnapshotRow } from "../../../../lib/api";

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

  const open = useCallback(
    async (snapshotId: string) => {
      try {
        setSelected(await api.getSnapshot(id, snapshotId));
      } catch (e: any) {
        setError(e.message || "Kanıt kopyası açılamadı");
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

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
      <section aria-labelledby="evidence-title" className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h2 id="evidence-title" className="text-sm font-semibold">
            Kanıt arşivi
          </h2>
          <form
            className="flex gap-2"
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
              className="rounded border border-line px-2 py-1 text-[13px]"
            />
            <button type="submit" className="rounded border border-line px-2 py-1 text-[13px]">
              Ara
            </button>
          </form>
        </div>
        {error && (
          <p role="alert" className="flex items-center gap-1 text-[13px] text-danger">
            <AlertTriangle size={14} aria-hidden /> {error}
          </p>
        )}
        {loading ? (
          <p className="flex items-center gap-1 text-ink-3">
            <Loader2 size={14} className="animate-spin" aria-hidden /> Yükleniyor…
          </p>
        ) : items.length === 0 ? (
          <p className="text-ink-3">
            Henüz arşivlenmiş sayfa yok. AI araştırma okuduğu sayfaları buraya kaydeder.
          </p>
        ) : (
          <ul className="divide-y divide-line rounded border border-line">
            {items.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  onClick={() => void open(row.id)}
                  aria-current={selected?.id === row.id}
                  className="w-full px-3 py-2 text-left hover:bg-surface-2"
                >
                  <p className="truncate text-[13px] font-medium">{row.title || row.url}</p>
                  <p className="truncate text-[12px] text-ink-3">{row.url}</p>
                  <p className="text-[11px] text-ink-3">
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
          <article className="space-y-2 rounded border border-line p-3">
            <h3 className="text-sm font-semibold">{selected.title || selected.url}</h3>
            <a
              href={selected.url}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex items-center gap-1 break-all text-[12px] text-accent-ink hover:underline"
            >
              {selected.url}
              <ExternalLink size={11} aria-hidden />
            </a>
            <p className="flex items-center gap-1 text-[12px]">
              {selected.integrity_ok ? (
                <>
                  <ShieldCheck size={14} aria-hidden /> Bütünlük doğrulandı
                </>
              ) : (
                <>
                  <ShieldAlert size={14} aria-hidden /> Bütünlük doğrulanamadı
                </>
              )}
            </p>
            <dl className="numeric grid grid-cols-[auto_1fr] gap-x-3 text-[11px] text-ink-3">
              <dt>içerik sha256</dt>
              <dd className="break-all">{selected.sha256}</dd>
              <dt>metin sha256</dt>
              <dd className="break-all">{selected.text_sha256}</dd>
              <dt>alındı</dt>
              <dd>{formatDate(selected.fetched_at)}</dd>
              <dt>saklama sonu</dt>
              <dd>{formatDate(selected.expires_at)}</dd>
            </dl>
            <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded bg-surface-2 p-2 text-[12px]">
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
