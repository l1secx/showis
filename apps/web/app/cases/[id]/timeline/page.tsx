"use client";

/**
 * Zaman çizelgesi sekmesi — mevcut API uçlarından olay üretir.
 *
 * Kaynaklar (yeni backend ucu yok):
 * * `GET /cases/{id}/audit` — sistem olayları (tarama, havuz, muhakeme…)
 * * `GET /cases/{id}/findings` — doğrulanmış bulgular (`verified_at`)
 * * `GET /cases/{id}/evidence/snapshots` — kanıt kopyaları (`fetched_at`)
 * * `GET /cases/{id}/reasoning/timeline` — tarihli havuz kayıtları
 *
 * Her kaynak bağımsız yüklenir: biri hata verse diğerleri çizilir, hata
 * şerit üstünde kısa bir uyarı olarak görünür.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import { api } from "../../../../lib/api";
import { normalizeEvents, type NormalizeInput } from "../../../../lib/timeline/model";
import { platformMeta } from "../platform-meta";
import { TimelineView } from "./TimelineView";
import { timelineActionLabel } from "./labels";

type SourceKey = "audit" | "findings" | "evidence" | "records";

const SOURCE_LABELS_TR: Record<SourceKey, string> = {
  audit: "Denetim kaydı",
  findings: "Bulgular",
  evidence: "Kanıt arşivi",
  records: "Tarihli kayıtlar",
};

const ALL_LOADING: Record<SourceKey, boolean> = {
  audit: true,
  findings: true,
  evidence: true,
  records: true,
};

export default function TimelinePage() {
  const { id } = useParams<{ id: string }>();
  const [input, setInput] = useState<NormalizeInput>({});
  const [loading, setLoading] = useState<Record<SourceKey, boolean>>(ALL_LOADING);
  const [errors, setErrors] = useState<Partial<Record<SourceKey, string>>>({});
  const [reloadKey, setReloadKey] = useState(0);

  const load = useCallback(async () => {
    setInput({});
    setErrors({});
    setLoading(ALL_LOADING);

    const fail = (key: SourceKey, error: unknown) => {
      const message =
        error instanceof Error && error.message ? error.message : "Kaynak yüklenemedi";
      setErrors((current) => ({ ...current, [key]: message }));
    };
    const done = (key: SourceKey) => {
      setLoading((current) => ({ ...current, [key]: false }));
    };

    const audit = api
      .getAudit(id)
      .then((rows) => setInput((current) => ({ ...current, audit: rows })))
      .catch((error) => fail("audit", error))
      .finally(() => done("audit"));

    const findings = api
      .listFindings(id)
      .then((rows) => setInput((current) => ({ ...current, findings: rows })))
      .catch((error) => fail("findings", error))
      .finally(() => done("findings"));

    const evidence = api
      .listSnapshots(id)
      .then((page) =>
        setInput((current) => ({ ...current, evidence: page.items }))
      )
      .catch((error) => fail("evidence", error))
      .finally(() => done("evidence"));

    const records = api
      .getReasoningTimeline(id)
      .then((page) => setInput((current) => ({ ...current, records: page.items })))
      .catch((error) => fail("records", error))
      .finally(() => done("records"));

    await Promise.all([audit, findings, evidence, records]);
  }, [id]);

  useEffect(() => {
    void load();
  }, [load, reloadKey]);

  const events = useMemo(
    () =>
      normalizeEvents(input, {
        actionLabel: timelineActionLabel,
        platformLabel: (platform) => platformMeta(platform).label,
      }),
    [input]
  );

  const failed = (Object.keys(errors) as SourceKey[]).filter((key) => errors[key]);
  const stillLoading = (Object.keys(loading) as SourceKey[]).some((key) => loading[key]);

  return (
    <section aria-labelledby="timeline-title" className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 id="timeline-title" className="section-heading">
            Olay zaman çizelgesi
          </h2>
          <p className="page-subtitle">
            Denetim kaydı, doğrulanmış bulgular, kanıt kopyaları ve tarihli havuz kayıtları tek
            eksende. Şeritler kaynağa göre ayrılır; yakın olaylar küme olarak çizilir.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {stillLoading && (
            <span className="inline-flex items-center gap-1.5 text-[12px] text-ink-3">
              <Loader2 size={13} className="animate-spin" aria-hidden /> Kaynaklar yükleniyor
            </span>
          )}
          <button
            type="button"
            className="btn-secondary btn-sm"
            onClick={() => setReloadKey((value) => value + 1)}
            disabled={stillLoading}
          >
            <RefreshCw size={13} aria-hidden />
            Yenile
          </button>
        </div>
      </div>

      {failed.length > 0 && (
        <div
          role="alert"
          className="rounded-[6px] border border-warn-line bg-warn-soft px-3 py-2 text-[12.5px] text-warn"
        >
          <p className="flex items-center gap-1.5 font-medium">
            <AlertTriangle size={13} aria-hidden />
            Bazı kaynaklar okunamadı; kalan olaylar gösteriliyor.
          </p>
          <ul className="mt-1 space-y-0.5">
            {failed.map((key) => (
              <li key={key}>
                {SOURCE_LABELS_TR[key]}: <span className="text-warn/90">{errors[key]}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <TimelineView events={events} />
    </section>
  );
}
