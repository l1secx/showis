"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { AlertTriangle, Check, Loader2, RefreshCw } from "lucide-react";
import { api, type WatchAlertRow } from "../../../../lib/api";

const KIND_TR: Record<string, string> = {
  breach: "Sızıntı",
  account: "Yeni hesap",
};

export default function WatchPage() {
  const { id } = useParams<{ id: string }>();
  const [alerts, setAlerts] = useState<WatchAlertRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setAlerts((await api.listWatchAlerts(id)).items);
      setError(null);
    } catch (e: any) {
      setError(e.message || "Uyarılar yüklenemedi");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const result = await api.runWatch(id);
      setNotice(
        result.total
          ? `${result.total} yeni uyarı (${result.breach} sızıntı, ${result.account} hesap).`
          : "Yeni bulgu yok."
      );
      await load();
    } catch (e: any) {
      setError(e.message || "İzleme çalıştırılamadı");
    } finally {
      setBusy(false);
    }
  };

  const markSeen = async (alertId: string) => {
    try {
      const updated = await api.markWatchAlertSeen(id, alertId);
      setAlerts((rows) => rows.map((row) => (row.id === alertId ? updated : row)));
    } catch (e: any) {
      setError(e.message || "Uyarı işaretlenemedi");
    }
  };

  return (
    <section aria-labelledby="watch-title" className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h2 id="watch-title" className="text-sm font-semibold">
            Kayıtlı izleme
          </h2>
          <p className="text-[12px] text-ink-3">
            Havuzdaki e-postalar için yeni sızıntılar ve izleme taramalarında yeni çıkan hesaplar.
            Otomatik çalışma için vakada izleme açık olmalıdır.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void run()}
          disabled={busy}
          className="inline-flex items-center gap-1 rounded border border-line px-2 py-1 text-[13px] disabled:opacity-50"
        >
          {busy ? (
            <Loader2 size={14} className="animate-spin" aria-hidden />
          ) : (
            <RefreshCw size={14} aria-hidden />
          )}
          Şimdi kontrol et
        </button>
      </div>
      {notice && (
        <p role="status" className="text-[13px] text-ink-2">
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" className="flex items-center gap-1 text-[13px] text-danger">
          <AlertTriangle size={14} aria-hidden /> {error}
        </p>
      )}
      {loading ? (
        <p className="text-ink-3">Yükleniyor…</p>
      ) : alerts.length === 0 ? (
        <p className="text-ink-3">Henüz uyarı yok.</p>
      ) : (
        <ul className="divide-y divide-line rounded border border-line">
          {alerts.map((alert) => (
            <li
              key={alert.id}
              className={`flex items-start justify-between gap-3 px-3 py-2 ${
                alert.seen_at ? "opacity-60" : ""
              }`}
            >
              <div className="min-w-0">
                <p className="text-[13px] font-medium">{alert.title}</p>
                <p className="text-[11px] text-ink-3">
                  {KIND_TR[alert.kind] || alert.kind} ·{" "}
                  {alert.created_at ? new Date(alert.created_at).toLocaleString("tr-TR") : "—"}
                </p>
              </div>
              {!alert.seen_at && (
                <button
                  type="button"
                  onClick={() => void markSeen(alert.id)}
                  className="inline-flex shrink-0 items-center gap-1 rounded border border-line px-2 py-0.5 text-[12px]"
                >
                  <Check size={12} aria-hidden /> Okundu
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
