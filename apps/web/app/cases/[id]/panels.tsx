"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowUpRight,
  Camera,
  Check,
  ChevronDown,
  Download,
  Eye,
  EyeOff,
  FileCode2,
  Fingerprint,
  Layers,
  Loader2,
  Play,
  RefreshCw,
  Search,
  Square,
  X,
} from "lucide-react";
import {
  api,
  type AuditEntry,
  type CaseComment,
  type CaseDiff,
  type CaseMonitor,
  type EvidenceItem,
  type FindingItem,
  type ReportInfo,
} from "../../../lib/api";
import type { CaseReportJson } from "../../../lib/types";
import { platformMeta } from "./platform-meta";
import { formatDuration, useScanSession } from "./scan-context";
import { useTheme } from "../../components/use-theme";
import { auditActionLabel, edgeTypeLabel, timelineEventLabel } from "./vocabulary";

/** Durum, nokta + metin ile anlatılır. Rozet çorbası yok. */
const STATUS_TONE: Record<string, { dot: string; text: string }> = {
  queued: { dot: "bg-ink-4", text: "text-ink-3" },
  running: { dot: "bg-accent", text: "text-accent-ink" },
  completed: { dot: "bg-ok", text: "text-ok" },
  failed: { dot: "bg-danger", text: "text-danger" },
  cancelled: { dot: "bg-ink-4", text: "text-ink-3" },
  lead: { dot: "bg-warn", text: "text-warn" },
  candidate: { dot: "bg-warn", text: "text-warn" },
  confirmed: { dot: "bg-ok", text: "text-ok" },
  rejected: { dot: "bg-danger", text: "text-danger" },
  done: { dot: "bg-ok", text: "text-ok" },
  error: { dot: "bg-danger", text: "text-danger" },
  pending: { dot: "bg-ink-4", text: "text-ink-3" },
  active: { dot: "bg-ok", text: "text-ok" },
  archived: { dot: "bg-ink-4", text: "text-ink-3" },
};

const STATUS_TR: Record<string, string> = {
  queued: "sırada",
  running: "çalışıyor",
  completed: "tamamlandı",
  failed: "başarısız",
  cancelled: "iptal",
  lead: "ipucu",
  candidate: "aday",
  confirmed: "onaylandı",
  rejected: "reddedildi",
  done: "tamamlandı",
  error: "hata",
  pending: "bekliyor",
  active: "aktif",
  archived: "arşiv",
};

export function Badge({ value }: { value: string }) {
  const tone = STATUS_TONE[value] ?? { dot: "bg-ink-4", text: "text-ink-3" };
  return (
    <span className={`status ${tone.text}`}>
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${tone.dot}`} aria-hidden />
      {STATUS_TR[value] || value}
    </span>
  );
}

/** Platform rozeti — `platform-meta.ts` etiket/renklerini kullanır, bilinmeyende nötr fallback.
 *  Açık temada markanın koyu rengi, koyu temada markanın açık tonu (`accent`)
 *  kullanılır — koyu zeminde koyu marka rengi görünmezdi. */
export function PlatformBadge({ platform }: { platform: string }) {
  const meta = platformMeta(platform);
  const theme = useTheme();
  const dot = theme === "dark" ? meta.accent : meta.color;
  return (
    <span
      className="inline-flex items-center gap-1.5 text-[12px] text-ink-3"
      title={meta.label === platform ? undefined : platform}
    >
      <span
        className="h-2 w-2 shrink-0 rounded-[1px]"
        style={{ backgroundColor: dot }}
        aria-hidden
      />
      {meta.label}
    </span>
  );
}

const MODES = [
  { value: "O1", label: "O1 — Hızlı", desc: "Arama ve API çağrıları, site kontrolü yok" },
  { value: "O2", label: "O2 — Standart", desc: "Arama, API ve 6 kullanıcı adı site kontrolü" },
  { value: "O3", label: "O3 — Derin", desc: "Standart mod ve tarayıcı ile 10 kullanıcı adı" },
];

/* ---------------- Tarama sekmesi ---------------- */

export function ScanTab({
  caseId,
  autoStart = false,
  initialMode,
}: {
  caseId: string;
  autoStart?: boolean;
  initialMode?: string;
}) {
  const { scan, events, mode, setMode, estimate, starting, error, start, cancel, scanId } =
    useScanSession();
  const [now, setNow] = useState(() => Date.now());
  const autoStarted = useRef(false);
  const [monitor, setMonitor] = useState<CaseMonitor | null>(null);
  const [monitorBusy, setMonitorBusy] = useState(false);
  const [webhook, setWebhook] = useState("");

  useEffect(() => {
    setWebhook(monitor?.webhook_url ?? "");
  }, [monitor?.webhook_url]);

  useEffect(() => {
    api.getMonitor(caseId).then(setMonitor).catch(() => setMonitor(null));
  }, [caseId]);

  const saveMonitor = async (patch: Partial<CaseMonitor>) => {
    if (!monitor || monitorBusy) return;
    const next = { ...monitor, ...patch };
    setMonitor(next);
    setMonitorBusy(true);
    try {
      setMonitor(
        await api.updateMonitor(caseId, {
          enabled: next.enabled,
          interval_hours: Number(next.interval_hours),
          mode: next.mode,
        })
      );
    } catch {
      api.getMonitor(caseId).then(setMonitor).catch(() => {});
    } finally {
      setMonitorBusy(false);
    }
  };

  const saveWebhook = async () => {
    setMonitorBusy(true);
    try {
      const result = await api.updateWebhook(caseId, webhook.trim() || null);
      setMonitor((current) =>
        current ? { ...current, webhook_url: result.webhook_url } : current
      );
    } catch {
      /* alan sessizce eski değerde kalır */
    } finally {
      setMonitorBusy(false);
    }
  };

  // Modu URL'den bir kez uygula.
  useEffect(() => {
    if (initialMode) setMode(initialMode);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Yeni vakadan gelindiyse taramayi otomatik baslat.
  useEffect(() => {
    if (!autoStart || autoStarted.current || scanId || starting) return;
    autoStarted.current = true;
    void start();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoStart, scanId, starting]);

  const running = scan?.status === "running" || scan?.status === "queued";

  // Gecen sure sayaci
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [running]);

  const startedAt = scan?.started_at ? new Date(scan.started_at).getTime() : null;
  const elapsed = startedAt ? Math.max(0, (now - startedAt) / 1000) : 0;
  const eta = estimate?.seconds ?? 0;
  const remaining = eta ? Math.max(0, eta - elapsed) : 0;
  const progress = eta ? Math.min(100, (elapsed / eta) * 100) : 0;

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
      <div className="space-y-5 lg:col-span-3">
        {monitor && (
          <section className="panel">
            <div className="panel-head">
              <h2 className="panel-title">Otomatik izleme</h2>
              <span className={monitor.enabled ? "status text-ok" : "status text-ink-3"}>
                <span
                  className={`h-1.5 w-1.5 rounded-full ${monitor.enabled ? "bg-ok" : "bg-ink-4"}`}
                  aria-hidden
                />
                {monitor.enabled ? "açık" : "kapalı"}
              </span>
            </div>
            <div className="flex flex-wrap items-end gap-4 p-5">
              <label className="flex cursor-pointer items-center gap-2 text-[12.5px] text-ink-2">
                <input
                  type="checkbox"
                  checked={monitor.enabled}
                  disabled={monitorBusy}
                  onChange={(e) => void saveMonitor({ enabled: e.target.checked })}
                  className="h-3.5 w-3.5 rounded-[3px] accent-[rgb(var(--accent))]"
                />
                Vakayı düzenli olarak yeniden tara
              </label>
              <label className="flex items-center gap-2 text-[12px] text-ink-3">
                Aralık
                <select
                  value={monitor.interval_hours}
                  disabled={monitorBusy || !monitor.enabled}
                  onChange={(e) =>
                    void saveMonitor({ interval_hours: Number(e.target.value) })
                  }
                  className="select w-auto py-1 text-[12px]"
                >
                  <option value={6}>6 saat</option>
                  <option value={12}>12 saat</option>
                  <option value={24}>24 saat</option>
                  <option value={72}>3 gün</option>
                  <option value={168}>7 gün</option>
                </select>
              </label>
              <label className="flex items-center gap-2 text-[12px] text-ink-3">
                Mod
                <select
                  value={monitor.mode}
                  disabled={monitorBusy || !monitor.enabled}
                  onChange={(e) => void saveMonitor({ mode: e.target.value })}
                  className="select w-auto py-1 text-[12px]"
                >
                  <option value="O1">O1</option>
                  <option value="O2">O2</option>
                  <option value="O3">O3</option>
                </select>
              </label>
              <p className="ml-auto text-[11.5px] text-ink-3">
                {monitor.last_scan_at
                  ? `Son izleme: ${new Date(monitor.last_scan_at).toLocaleString("tr-TR")}`
                  : "Henüz izleme taraması yok"}
              </p>
              <label className="w-full">
                <span className="label">Webhook (scan.completed)</span>
                <div className="flex gap-2">
                  <input
                    className="input font-mono"
                    value={webhook}
                    onChange={(e) => setWebhook(e.target.value)}
                    placeholder="https://ornek.example/webhook"
                  />
                  <button
                    type="button"
                    onClick={saveWebhook}
                    disabled={monitorBusy}
                    className="btn-secondary btn-sm"
                  >
                    Kaydet
                  </button>
                </div>
              </label>
            </div>
          </section>
        )}

        {/* Tarama modu */}
        <section className="panel">
          <div className="panel-head">
            <h2 className="panel-title">Tarama modu</h2>
            {running && (
              <span className="status text-accent-ink">
                <span className="pulse-live h-1.5 w-1.5 rounded-full bg-accent" aria-hidden />
                canlı
              </span>
            )}
          </div>

          <div className="p-5">
            <fieldset className="grid gap-2 sm:grid-cols-3">
              <legend className="sr-only">Tarama modu</legend>
              {MODES.map((m) => {
                const active = mode === m.value;
                return (
                  <label
                    key={m.value}
                    className={`flex cursor-pointer flex-col gap-1 rounded-[6px] border px-3 py-2.5 transition-colors ${
                      active
                        ? "border-accent bg-accent-soft"
                        : "border-line-2 bg-surface hover:border-ink-4"
                    } ${running ? "cursor-not-allowed opacity-60" : ""}`}
                  >
                    <span className="flex items-center gap-2">
                      <input
                        type="radio"
                        name="scan-mode"
                        value={m.value}
                        checked={active}
                        disabled={running}
                        onChange={() => setMode(m.value)}
                        className="h-3.5 w-3.5 shrink-0 accent-[rgb(var(--accent))]"
                      />
                      <span className="text-[13px] font-medium text-ink">{m.label}</span>
                    </span>
                    <span className="text-[12px] leading-snug text-ink-3">{m.desc}</span>
                  </label>
                );
              })}
            </fieldset>

            {/* Tahmin: veri şeridi */}
            {/* Tahmin: panelin içinde ikinci bir kutu değil, ayraçla ayrılmış
                bir ölçü satırı. */}
            <dl className="mt-4 flex flex-wrap items-center gap-x-7 gap-y-2 border-t border-line pt-3.5 text-[12px] text-ink-3">
              <div className="flex items-center gap-2">
                <dt className="text-[12px] text-ink-3">Tahmini üst sınır</dt>
                <dd className="numeric font-medium text-ink">
                  {estimate ? formatDuration(estimate.seconds) : "hesaplanıyor"}
                </dd>
              </div>
              {estimate && (
                <>
                  <div className="flex items-center gap-2">
                    <dt className="text-[12px] text-ink-3">Görev</dt>
                    <dd className="numeric font-medium text-ink">{estimate.tasks}</dd>
                  </div>
                  <div className="flex items-center gap-2">
                    <dt className="text-[12px] text-ink-3">İstek</dt>
                    <dd className="numeric font-medium text-ink">{estimate.requests}</dd>
                  </div>
                </>
              )}
            </dl>

            {estimate && estimate.steps.length > 0 && (
              <ul className="mt-3 space-y-1">
                {estimate.steps.map((s) => (
                  <li key={s.key} className="flex items-center justify-between gap-3 text-[12px]">
                    <span className="text-ink-3">{s.label}</span>
                    <span className="numeric text-ink-3">{s.tasks}</span>
                  </li>
                ))}
              </ul>
            )}

            <div className="mt-5 flex flex-wrap gap-2 border-t border-line pt-4">
              <button onClick={start} disabled={starting || running} className="btn-primary">
                {starting ? (
                  <Loader2 size={14} className="animate-spin" aria-hidden />
                ) : (
                  <Play size={14} aria-hidden />
                )}
                {running ? "Tarama sürüyor" : scan ? "Yeniden tara" : "Taramayı başlat"}
              </button>
              {running && (
                <button onClick={cancel} className="btn-secondary">
                  <Square size={12} aria-hidden />
                  İptal
                </button>
              )}
            </div>

            {error && (
              <p
                role="alert"
                className="mt-4 flex items-start gap-2 rounded-[6px] border border-danger-line bg-danger-soft px-3 py-2 text-[12.5px] text-danger"
              >
                <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden />
                {error}
              </p>
            )}
          </div>
        </section>

        {/* İlerleme */}
        {scan && (
          <section className="panel">
            <div className="panel-head">
              <h2 className="panel-title">İlerleme</h2>
              <div className="flex items-center gap-3">
                <Badge value={scan.status} />
                <span className="numeric text-ink-3">{scan.mode}</span>
              </div>
            </div>

            <div className="p-5">
              <div
                className="meter"
                role="progressbar"
                aria-valuenow={Math.round(progress)}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label="Tarama ilerlemesi"
              >
                <div
                  className={`h-full ${
                    scan.status === "failed"
                      ? "bg-danger"
                      : scan.status === "completed"
                        ? "bg-ok"
                        : "bg-accent"
                  }`}
                  style={{ width: `${scan.status === "completed" ? 100 : progress}%` }}
                />
              </div>

              <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2 text-[12px] text-ink-3">
                <span>
                  Geçen{" "}
                  <span className="numeric text-ink-2">{formatDuration(elapsed)}</span>
                  {running && eta > 0 && (
                    <>
                      {" — kalan "}
                      <span className="numeric text-ink-2">{formatDuration(remaining)}</span>
                    </>
                  )}
                </span>
                <span>
                  <span className="numeric text-ink-2">{scan.tasks_count}</span> görev,{" "}
                  <span className="numeric text-ink-2">
                    {scan.tasks.filter((t) => t.status === "done").length}
                  </span>{" "}
                  tamam
                </span>
              </div>

              <div className="mt-4 max-h-80 overflow-y-auto rounded-[6px] border border-line">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th scope="col">Bağlayıcı</th>
                      <th scope="col" className="hidden sm:table-cell">
                        Görev
                      </th>
                      <th scope="col" className="text-right">Gözlem</th>
                      <th scope="col">Durum</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scan.tasks.map((t) => (
                      <tr key={t.id}>
                        <td className="numeric text-ink-2">{t.connector}</td>
                        <td className="hidden text-ink-3 sm:table-cell">
                          {t.kind}
                          {t.error && (
                            <span className="ml-2 text-danger">{t.error.slice(0, 60)}</span>
                          )}
                        </td>
                        <td className="numeric text-right text-ink-3">{t.observation_count}</td>
                        <td>
                          <Badge value={t.status} />
                        </td>
                      </tr>
                    ))}
                    {scan.tasks.length === 0 && (
                      <tr>
                        <td colSpan={4} className="px-4 py-6 text-center text-ink-3">
                          Görev planlanıyor.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        )}
      </div>

      {/* Olay akışı */}
      <section className="panel flex flex-col lg:col-span-2">
        <div className="panel-head">
          <h2 className="panel-title">Canlı olay akışı</h2>
          <span className="numeric text-ink-3">{events.length} olay</span>
        </div>
        <div className="max-h-[560px] flex-1 overflow-y-auto p-4 font-mono text-[12px] leading-relaxed">
          {events.length === 0 ? (
            <p className="font-sans text-[12.5px] text-ink-3">
              Tarama başlatıldığında bağlayıcı çıktıları burada akar.
            </p>
          ) : (
            <div className="space-y-0.5">
              {events.map((ev, i) => {
                const bad = ev.type.includes("fail") || ev.type.includes("error");
                return (
                  <div key={i} className="flex gap-2">
                    <span className="shrink-0 text-ink-3">{ev.at}</span>
                    <span className={`shrink-0 ${bad ? "text-danger" : "text-accent-ink"}`}>
                      {ev.type}
                    </span>
                    <span className="truncate text-ink-3">
                      {JSON.stringify(ev.data).slice(0, 140)}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

/* ---------------- Bulgular sekmesi ---------------- */

type FindingFilter = "all" | "confirmed" | "candidate" | "lead" | "rejected";
type FindingSort = "score_desc" | "corroboration_desc" | "authenticity_desc" | "score_asc";

const FINDING_SORTS: { value: FindingSort; label: string; hint: string }[] = [
  { value: "score_desc", label: "Güven skoru (yüksek)", hint: "En yüksek güven skoru önce" },
  {
    value: "corroboration_desc",
    label: "Bağımsız sinyal",
    hint: "Skoru en çok bağımsız sinyal destekleyen bulgu önce",
  },
  {
    value: "authenticity_desc",
    label: "Hesap güvenilirliği",
    hint: "Sahte/spam sinyali en düşük hesaplar önce",
  },
  { value: "score_asc", label: "Güven skoru (düşük)", hint: "Zayıf sinyaller önce" },
];

const FINDING_FILTERS: { value: FindingFilter; label: string; hint: string }[] = [
  { value: "all", label: "Tümü", hint: "Reddedilenler gizliyken kalan tüm bulgular" },
  { value: "confirmed", label: "Onaylanan", hint: "Güven skoru 0.80 ve üzeri" },
  { value: "candidate", label: "Aday", hint: "Güven skoru 0.50 – 0.79" },
  {
    value: "lead",
    label: "İz",
    hint: "İpucu (lead): isim/okul benzerliği veya birebir kullanıcı adı eşleşmesi olan, doğrulanmamış sonuç",
  },
  { value: "rejected", label: "Reddedilen", hint: "Güven skoru 0.50 altı ve anlamlı sinyal yok" },
];

/** Skor kırılımı sinyalleri — packages/engine/showis_engine/resolver.py ağırlıkları. */
const SIGNAL_META: Record<string, { label: string; hint: string }> = {
  username_exact_match: {
    label: "Kullanıcı adı eşleşmesi",
    hint: "Aynı kullanıcı adı en az 2 platformda (0.30)",
  },
  handle_exact: {
    label: "Hedef kullanıcı adı eşleşmesi",
    hint: "Tek platformda birebir kullanıcı adı eşleşmesi (0.15)",
  },
  name_similarity: {
    label: "İsim benzerliği",
    hint: "Hedef ad ile profil adı arasındaki benzerlik (0.20)",
  },
  cross_link: { label: "Çapraz bağlantı", hint: "Profiller arası doğrulanmış bağlantı (0.25)" },
  bio_location_company: {
    label: "Bio, konum, kurum veya okul",
    hint: "Eşleşen her alan 0.05 puan (en fazla 0.15)",
  },
  commit_email_or_pgp: {
    label: "Commit e-postası veya PGP",
    hint: "Kod katkısı üzerinden kimlik doğrulama (0.15)",
  },
  avatar_exact_hash: {
    label: "Avatar hash eşleşmesi",
    hint: "Birebir aynı profil görseli (0.10)",
  },
  identifier_match: {
    label: "Hedef kimliği eşleşmesi",
    hint: "E-posta, telefon, domain veya IP birebir eşleşti (0.50)",
  },
  contradiction: { label: "Çelişki", hint: "Ad/konum uyuşmazlığı — skoru düşürür (−0.20)" },
};

/** Kırılım çubuğunun ölçeği: en büyük tek sinyal ağırlığı. */
const MAX_SIGNAL_WEIGHT = 0.3;

const findingsFilterKey = (caseId: string) => `showis:findings:${caseId}`;
const findingsRejectedKey = (caseId: string) => `showis:findings:show-rejected:${caseId}`;

function readStoredFilter(caseId: string): FindingFilter {
  if (typeof window === "undefined") return "all";
  const raw = window.sessionStorage.getItem(findingsFilterKey(caseId));
  // Eski sürüm "relevant" (aday ve üzeri) yazıyordu; yeni varsayılan: Tümü + reddedilenler kapalı.
  if (!raw || raw === "relevant") return "all";
  return FINDING_FILTERS.some((f) => f.value === raw) ? (raw as FindingFilter) : "all";
}

function readStoredShowRejected(caseId: string): boolean {
  if (typeof window === "undefined") return false;
  return window.sessionStorage.getItem(findingsRejectedKey(caseId)) === "1";
}

/* --- T-301: authenticity (hesap güvenilirliği) --- */

/**
 * Güvenilirlik renk eşikleri (tek kaynak — T-301 spec):
 *   < 0.35 kırmızı · 0.35–0.70 amber · > 0.70 yeşil
 * Başka bir bileşende kopyalamayın; bu sabitleri import edin.
 */
export const LOW_AUTH_THRESHOLD = 0.35;
export const HIGH_AUTH_THRESHOLD = 0.7;

type FindingWithAuth = FindingItem;

const findingsLowAuthKey = (caseId: string) => `showis:findings:hide-low-auth:${caseId}`;

function readStoredHideLowAuth(caseId: string): boolean {
  if (typeof window === "undefined") return false;
  return window.sessionStorage.getItem(findingsLowAuthKey(caseId)) === "1";
}

function isLowAuthenticity(f: FindingWithAuth): boolean {
  return typeof f.authenticity === "number" && f.authenticity < LOW_AUTH_THRESHOLD;
}

export function authenticityTone(score: number): {
  text: string;
  bar: string;
  label: string;
} {
  if (score < LOW_AUTH_THRESHOLD) {
    return { text: "text-danger", bar: "bg-danger", label: "düşük güvenilirlik" };
  }
  if (score < HIGH_AUTH_THRESHOLD) {
    return { text: "text-warn", bar: "bg-warn", label: "orta güvenilirlik" };
  }
  return { text: "text-ok", bar: "bg-ok", label: "güvenilir hesap" };
}

/**
 * Authenticity göstergesi. `authenticity` yoksa (eski kayıt) hiçbir şey render etmez —
 * geriye uyum için tek kontrol noktası burasıdır.
 */
function AuthenticityChip({
  score,
  reasons,
}: {
  score: number | null | undefined;
  reasons?: string[] | null;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  if (typeof score !== "number") return null;

  const pct = Math.round(Math.max(0, Math.min(1, score)) * 100);
  const tone = authenticityTone(score);
  const hasReasons = Boolean(reasons && reasons.length > 0);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span
        className={`inline-flex items-center gap-1.5 text-[12px] font-medium ${tone.text}`}
        title={
          hasReasons
            ? `Güvenilirlik ${pct}% — ${tone.label}\n${(reasons ?? []).join("\n")}`
            : `Güvenilirlik ${pct}% — ${tone.label}`
        }
      >
        <Fingerprint size={11} aria-hidden />
        güvenilirlik <span className="numeric">{pct}%</span>
      </span>
      <span className="meter w-14" aria-hidden>
        <span className={`block h-full ${tone.bar}`} style={{ width: `${Math.max(4, pct)}%` }} />
      </span>
      {hasReasons && (
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((v) => !v)}
          className={`inline-flex items-center gap-1 rounded text-[12px] ${tone.text} underline-offset-2 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/25`}
        >
          {reasons!.length} neden
          <ChevronDown
            size={10}
            aria-hidden
            className={`transition-transform ${open ? "" : "-rotate-90"}`}
          />
        </button>
      )}
      {hasReasons && open && (
        <ul
          id={panelId}
          className="mt-1 w-full basis-full space-y-1 rounded-[6px] border border-line bg-sunken px-3 py-2 text-[12px] text-ink-3"
        >
          {reasons!.map((reason) => (
            <li key={reason} className="flex items-start gap-2">
              <span className={`mt-1.5 h-1 w-1 shrink-0 rounded-full ${tone.bar}`} aria-hidden />
              {reason}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ScoreBreakdown({
  breakdown,
  score,
}: {
  breakdown: Record<string, number>;
  score: number;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();

  const rows = Object.entries(breakdown)
    .filter(([key, value]) => key !== "total" && Number(value) !== 0)
    .map(([key, value]) => ({ key, value: Number(value), meta: SIGNAL_META[key] }))
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
  const total = typeof breakdown.total === "number" ? breakdown.total : score;

  return (
    <div className="mt-3">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 rounded text-[12px] text-ink-3 transition-colors hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/25"
      >
        <ChevronDown
          size={12}
          aria-hidden
          className={`transition-transform ${open ? "" : "-rotate-90"}`}
        />
        Skor kırılımı
        <span className="numeric text-ink-3">
          {rows.length} sinyal, toplam {Math.round(total * 100)}%
        </span>
      </button>

      {open && (
        <div id={panelId} className="mt-2 rounded-[6px] border border-line bg-sunken p-3">
          {rows.length === 0 ? (
            <p className="text-[12px] text-ink-3">Skor sinyali kaydı yok.</p>
          ) : (
            <ul className="space-y-2">
              {rows.map((row) => {
                const negative = row.value < 0;
                return (
                  <li key={row.key} className="grid grid-cols-[1fr_auto] items-center gap-3">
                    <span className="min-w-0">
                      <span
                        className={`block truncate text-[12px] ${
                          negative ? "text-danger" : "text-ink-2"
                        }`}
                        title={row.meta?.hint}
                      >
                        {row.meta?.label ?? row.key.replace(/_/g, " ")}
                      </span>
                      <span className="meter mt-1">
                        <span
                          className={`block h-full ${negative ? "bg-danger" : "bg-accent"}`}
                          style={{
                            width: `${Math.min(
                              100,
                              (Math.abs(row.value) / MAX_SIGNAL_WEIGHT) * 100
                            )}%`,
                          }}
                        />
                      </span>
                    </span>
                    <span
                      className={`numeric ${negative ? "text-danger" : "text-ink-2"}`}
                    >
                      {row.value > 0 ? "+" : ""}
                      {Math.round(row.value * 100)} puan
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
          <div className="mt-3 flex items-center justify-between border-t border-line pt-2 text-[12px]">
            <span className="text-ink-3">Toplam güven</span>
            <span className="numeric font-medium text-ink">{Math.round(total * 100)}%</span>
          </div>
        </div>
      )}
    </div>
  );
}

function FindingRecord({
  finding: f,
  evidence,
  expanded,
  highlighted = false,
  busy = false,
  capturing = false,
  onToggleEvidence,
  onVerify,
  onCapture,
}: {
  finding: FindingWithAuth;
  evidence?: EvidenceItem[];
  expanded: boolean;
  highlighted?: boolean;
  busy?: boolean;
  capturing?: boolean;
  onToggleEvidence: (id: string) => void;
  onVerify: (id: string, status: "confirmed" | "rejected") => void;
  onCapture?: (id: string) => void;
}) {
  const pct = Math.round(f.score * 100);
  const isLead = f.status === "lead";
  const evidencePanelId = useId();

  return (
    <article
      id={`finding-${f.id}`}
      tabIndex={highlighted ? -1 : undefined}
      className={`px-5 py-4 transition-colors focus:outline-none ${
        highlighted ? "bg-accent-soft" : "hover:bg-hover"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <h2 className="truncate text-[14px] font-semibold text-ink">{f.label}</h2>
            <PlatformBadge platform={f.platform} />
            <Badge value={f.status} />
            {f.cluster && f.cluster.size > 1 && (
              <span
                className="status text-ink-3"
                title={`${f.cluster.platforms.join(", ")} — ${f.cluster.evidence_count} kanıt`}
              >
                <Layers size={11} aria-hidden />
                {f.cluster.size} hesap aynı kümede
              </span>
            )}
          </div>

          {f.url && (
            <a
              href={f.url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-1 inline-flex max-w-full items-center gap-1.5 font-mono text-[12px] text-ink-3 transition-colors hover:text-accent-ink"
            >
              <span className="truncate">{f.url}</span>
              <ArrowUpRight size={11} className="shrink-0 text-ink-3" aria-hidden />
            </a>
          )}

          {f.explanation && (
            <p className="mt-2 max-w-[28rem] text-[12.5px] leading-relaxed text-ink-2">
              {f.explanation}
            </p>
          )}

          {isLead && (
            <p className="mt-1.5 text-[12px] font-medium text-warn">
              Tekil kaynak; henüz doğrulanmadı.
            </p>
          )}
          {f.status === "rejected" && (
            <p className="mt-1.5 text-[12px] font-medium text-danger">
              Düşük güvenilirlikli veya ilgisiz sonuç olarak işaretlendi.
            </p>
          )}

          <div className="mt-2.5">
            <AuthenticityChip score={f.authenticity} reasons={f.authenticity_reasons} />
          </div>
        </div>

        {/* Skor: sayının kendisi önde, çubuk yalnızca destek. */}
        <div className="flex shrink-0 flex-col items-end gap-2">
          <div className="text-right">
            <p className="tabular text-[19px] font-semibold leading-none text-ink">{pct}</p>
            <p className="mt-1 text-[12px] text-ink-3">güven puanı</p>
          </div>
          <div className="meter w-24" aria-hidden>
            <div
              className={`h-full ${
                f.status === "confirmed"
                  ? "bg-ok"
                  : f.status === "rejected"
                    ? "bg-danger"
                    : "bg-accent"
              }`}
              style={{ width: `${Math.max(4, pct)}%` }}
            />
          </div>
          {((f.corroboration ?? 0) > 0 || (f.evidence_count ?? f.evidence_ids.length) > 0) && (
            <p
              className="text-right text-[11.5px] text-ink-3"
              title={
                (f.signals ?? [])
                  .map((key) => SIGNAL_META[key]?.label ?? key)
                  .join(", ") || undefined
              }
            >
              {(f.corroboration ?? 0) > 0 && (
                <>
                  <span className="numeric font-medium text-ink-2">{f.corroboration}</span>{" "}
                  bağımsız sinyal
                  <span className="mx-1 text-ink-4" aria-hidden>
                    ·
                  </span>
                </>
              )}
              <span className="numeric">{f.evidence_count ?? f.evidence_ids.length}</span>{" "}
              kanıt
            </p>
          )}

          {(f.status === "candidate" || f.status === "lead") && (
            <div className="mt-1 flex gap-2">
              <button
                onClick={() => onVerify(f.id, "confirmed")}
                disabled={busy}
                className="btn-secondary btn-sm"
              >
                {busy ? (
                  <Loader2 size={13} className="animate-spin" aria-hidden />
                ) : (
                  <Check size={13} aria-hidden />
                )}
                Onayla
              </button>
              <button
                onClick={() => onVerify(f.id, "rejected")}
                disabled={busy}
                className="btn-danger-quiet btn-sm"
              >
                <X size={13} aria-hidden />
                Reddet
              </button>
            </div>
          )}
        </div>
      </div>

      {f.breakdown && <ScoreBreakdown breakdown={f.breakdown} score={f.score} />}

      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={evidencePanelId}
        onClick={() => onToggleEvidence(f.id)}
        className="mt-3 inline-flex items-center gap-1.5 rounded text-[12px] text-ink-3 transition-colors hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/25"
      >
        <Eye size={12} aria-hidden />
        {expanded ? "Kanıtları gizle" : `Kanıtlar (${f.evidence_ids.length})`}
      </button>
      {f.url && onCapture && (
        <button
          type="button"
          onClick={() => onCapture(f.id)}
          disabled={capturing}
          className="ml-3 mt-3 inline-flex items-center gap-1.5 rounded text-[12px] text-ink-3 transition-colors hover:text-ink disabled:opacity-50"
          title="Sayfanın ekran görüntüsünü kanıt zincirine ekle"
        >
          {capturing ? (
            <Loader2 size={12} className="animate-spin" aria-hidden />
          ) : (
            <Camera size={12} aria-hidden />
          )}
          Ekran görüntüsü
        </button>
      )}

      {expanded && (
        <div id={evidencePanelId} className="mt-3 rounded-[6px] border border-line bg-sunken p-3">
          <p className="text-[12px] font-medium text-ink-2">Kanıt zinciri</p>
          <div className="mt-2 space-y-2.5">
            {(evidence || []).map((ev) => (
              <div key={ev.id} className="space-y-0.5">
                <a
                  href={ev.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex max-w-full items-center gap-1 break-all font-mono text-[12px] text-accent-ink underline decoration-accent-line underline-offset-2 hover:decoration-accent"
                >
                  {ev.source_url}
                  <ArrowUpRight size={10} className="shrink-0" aria-hidden />
                </a>
                <p className="numeric text-ink-3">
                  {ev.kind} — sha256 {ev.content_hash.slice(0, 16)}
                  {ev.http_status ? ` — HTTP ${ev.http_status}` : ""}
                  {ev.fetched_at ? ` — ${new Date(ev.fetched_at).toLocaleString("tr-TR")}` : ""}
                </p>
                {ev.preview && (
                  <p className="line-clamp-3 text-[12px] leading-snug text-ink-2">{ev.preview}</p>
                )}
              </div>
            ))}
          </div>
          {(evidence || []).length === 0 && (
            <p className="mt-2 text-[12px] text-warn">Kanıt kaydı yok.</p>
          )}
        </div>
      )}
    </article>
  );
}

export function FindingsTab({ caseId }: { caseId: string }) {
  const { findingsVersion, scan } = useScanSession();
  const [findings, setFindings] = useState<FindingWithAuth[]>([]);
  const [evidence, setEvidence] = useState<Record<string, EvidenceItem[]>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // Varsayılan: Tümü + reddedilenler kapalı (= aday ve üzeri görünür).
  const [filter, setFilter] = useState<FindingFilter>("all");
  const [showRejected, setShowRejected] = useState(false);
  // T-301: düşük güvenilirlikli hesapları gizle (varsayılan kapalı).
  const [hideLowAuth, setHideLowAuth] = useState(false);
  // OSINT görünümü: platform filtresi, serbest arama, sıralama ve küme gruplama.
  const [platformFilter, setPlatformFilter] = useState<string[]>([]);
  const [findingQuery, setFindingQuery] = useState("");
  const [findingSort, setFindingSort] = useState<FindingSort>("score_desc");
  const [grouped, setGrouped] = useState(true);
  const [collapsedClusters, setCollapsedClusters] = useState<string[]>([]);
  /** Karar eyleminin hatası — boşsa hata yok. */
  const [actionError, setActionError] = useState<{ message: string; detail?: string } | null>(
    null
  );
  /** Kararı gönderilen bulgu — düğmeyi kilitler. */
  const [verifying, setVerifying] = useState<string | null>(null);
  /** Ekran görüntüsü alınan bulgu. */
  const [capturing, setCapturing] = useState<string | null>(null);
  // T-303 → T-318: grafikten gelen entity (bulgular sekmesinde vurgulanır).
  const [focusEntity, setFocusEntity] = useState<string | null>(null);
  // Grafikten gelen kimlik kümesi filtresi (`?cluster=`).
  const [clusterFilter, setClusterFilter] = useState<string | null>(null);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  // Tercih yalnızca istemcide okunur: SSR/CSR hidrasyon uyuşmazlığı olmasın.
  useEffect(() => {
    const storedFilter = readStoredFilter(caseId);
    setFilter(storedFilter);
    setShowRejected(storedFilter === "rejected" ? true : readStoredShowRejected(caseId));
    setHideLowAuth(readStoredHideLowAuth(caseId));
  }, [caseId]);

  // Grafikten gelen `?entity=<id>` parametresi — filtre durumuna dokunmadan vurgular.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const readFocus = () => {
      const params = new URLSearchParams(window.location.search);
      const value = params.get("entity");
      setFocusEntity(value && value.trim() ? value.trim() : null);
      const cluster = params.get("cluster");
      setClusterFilter(cluster && cluster.trim() ? cluster.trim() : null);
    };
    readFocus();
    window.addEventListener("popstate", readFocus);
    return () => window.removeEventListener("popstate", readFocus);
  }, []);

  const clearFocus = () => {
    setFocusEntity(null);
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      url.searchParams.delete("entity");
      window.history.replaceState(null, "", url.pathname + url.search + url.hash);
    }
  };

  const clearClusterFilter = () => {
    setClusterFilter(null);
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      url.searchParams.delete("cluster");
      window.history.replaceState(null, "", url.pathname + url.search + url.hash);
    }
  };

  const load = useCallback(async () => {
    try {
      setFindings(await api.listFindings(caseId));
    } catch (e: any) {
      setErr(e.message);
    }
  }, [caseId]);

  useEffect(() => {
    void load();
  }, [load, findingsVersion]);

  const persist = (key: string, value: string) => {
    if (typeof window !== "undefined") window.sessionStorage.setItem(key, value);
  };

  const applyFilter = (value: FindingFilter) => {
    setFilter(value);
    persist(findingsFilterKey(caseId), value);
    // "Reddedilen" sekmesi seçildiyse anahtar da açık kalsın (görsel tutarlılık).
    if (value === "rejected" && !showRejected) {
      setShowRejected(true);
      persist(findingsRejectedKey(caseId), "1");
    }
  };

  const applyShowRejected = (next: boolean) => {
    if (!next && filter === "rejected") {
      // Reddedilenler kapatılırsa "Reddedilen" sekmesi boş kalmasın.
      setFilter("all");
      persist(findingsFilterKey(caseId), "all");
    }
    setShowRejected(next);
    persist(findingsRejectedKey(caseId), next ? "1" : "0");
  };

  const applyHideLowAuth = (next: boolean) => {
    setHideLowAuth(next);
    persist(findingsLowAuthKey(caseId), next ? "1" : "0");
  };

  const onTabKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    const last = FINDING_FILTERS.length - 1;
    let next = -1;
    if (event.key === "ArrowRight") next = index === last ? 0 : index + 1;
    else if (event.key === "ArrowLeft") next = index === 0 ? last : index - 1;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = last;
    if (next < 0) return;
    event.preventDefault();
    applyFilter(FINDING_FILTERS[next].value);
    tabRefs.current[next]?.focus();
  };

  const toggleEvidence = async (fid: string) => {
    if (expanded === fid) {
      setExpanded(null);
      return;
    }
    setExpanded(fid);
    if (!evidence[fid]) {
      try {
        const res = await api.getEvidence(fid);
        setEvidence((m) => ({ ...m, [fid]: res.evidence }));
      } catch {
        setEvidence((m) => ({ ...m, [fid]: [] }));
      }
    }
  };

  /**
   * Bulgu kararı (onayla / reddet). Hata durumda **sessizce yutulmaz**:
   * karar kaydedilmediyse analiste bunu söyle, yoksa düğmeye tekrar
   * basıp "neden değişmedi" diye aramak zorunda kalır.
   */
  const verify = async (fid: string, status: "confirmed" | "rejected") => {
    setVerifying(fid);
    setActionError(null);
    try {
      await api.verifyFinding(fid, status);
      await load();
    } catch (e: any) {
      setActionError({
        message:
          status === "confirmed"
            ? "Bulgu onaylanamadı. Bağlantıyı kontrol edip yeniden deneyin."
            : "Bulgu reddedilemedi. Bağlantıyı kontrol edip yeniden deneyin.",
        detail: e?.message,
      });
    } finally {
      setVerifying(null);
    }
  };

  const captureScreenshot = async (findingId: string) => {
    setCapturing(findingId);
    setActionError(null);
    try {
      await api.captureScreenshot(findingId);
      await load();
      const refreshed = await api.getEvidence(findingId);
      setEvidence((map) => ({ ...map, [findingId]: refreshed.evidence }));
    } catch (e: any) {
      setActionError({
        message: "Ekran görüntüsü alınamadı. Site robots/engel veya tarayıcı servisi kapalı olabilir.",
        detail: e?.message,
      });
    } finally {
      setCapturing(null);
    }
  };

  const counts: Record<FindingFilter, number> = {
    all: findings.length,
    confirmed: findings.filter((f) => f.status === "confirmed").length,
    candidate: findings.filter((f) => f.status === "candidate").length,
    lead: findings.filter((f) => f.status === "lead").length,
    rejected: findings.filter((f) => f.status === "rejected").length,
  };
  const hiddenRejected = showRejected ? 0 : counts.rejected;

  // Reddedilenler kapalıyken "Tümü" = aday ve üzeri.
  const statusFiltered = useMemo(
    () =>
      findings.filter((f) =>
        filter === "all" ? showRejected || f.status !== "rejected" : f.status === filter
      ),
    [findings, filter, showRejected]
  );

  // T-301: düşük güvenilirlikli hesaplar isteğe bağlı gizlenir (varsayılan kapalı).
  const visible = useMemo(
    () => (hideLowAuth ? statusFiltered.filter((f) => !isLowAuthenticity(f)) : statusFiltered),
    [statusFiltered, hideLowAuth]
  );
  const lowAuthCount = findings.filter(isLowAuthenticity).length;
  const hiddenLowAuth = statusFiltered.length - visible.length;

  // T-318: grafikten gelen entity — filtre state'ini değiştirmeden listeye sabitlenir.
  const focusFinding = useMemo(() => {
    if (!focusEntity) return null;
    return (
      findings.find(
        (f) =>
          f.entity_id === focusEntity ||
          f.id === focusEntity ||
          f.id === `finding:${focusEntity}`
      ) ?? null
    );
  }, [findings, focusEntity]);

  const activeCluster = useMemo(
    () =>
      clusterFilter
        ? findings.find((f) => f.cluster_id === clusterFilter)?.cluster ?? null
        : null,
    [findings, clusterFilter]
  );

  const platformOptions = useMemo(() => {
    const map = new Map<string, number>();
    // Sayılar, o anki durum/güvenilirlik filtresinden geçen kümele uyumlu olmalı;
    // aksi halde "GitHub 30" yazıp tıklanınca 0 sonuç görünür.
    visible.forEach((f) => map.set(f.platform, (map.get(f.platform) ?? 0) + 1));
    return [...map.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [visible]);

  // Platform + serbest metin (etiket/URL/platform) birlikte daraltır.
  const narrowed = useMemo(() => {
    const needle = findingQuery.trim().toLocaleLowerCase("tr");
    const platformSet = new Set(platformFilter);
    return visible.filter((f) => {
      if (clusterFilter && f.cluster_id !== clusterFilter) return false;
      if (platformSet.size > 0 && !platformSet.has(f.platform)) return false;
      if (!needle) return true;
      const haystack = `${f.label} ${f.url ?? ""} ${f.platform}`.toLocaleLowerCase("tr");
      return haystack.includes(needle);
    });
  }, [visible, platformFilter, findingQuery, clusterFilter]);

  const sortedVisible = useMemo(() => {
    const rows = [...narrowed];
    rows.sort((a, b) => {
      if (findingSort === "corroboration_desc") {
        const diff = (b.corroboration ?? 0) - (a.corroboration ?? 0);
        if (diff !== 0) return diff;
        return b.score - a.score || a.id.localeCompare(b.id);
      }
      if (findingSort === "authenticity_desc") {
        const left = a.authenticity ?? -1;
        const right = b.authenticity ?? -1;
        if (left !== right) return right - left;
        return b.score - a.score || a.id.localeCompare(b.id);
      }
      if (findingSort === "score_asc") {
        return a.score - b.score || a.id.localeCompare(b.id);
      }
      return b.score - a.score || a.id.localeCompare(b.id);
    });
    return rows;
  }, [narrowed, findingSort]);

  const shown = useMemo(
    () =>
      focusFinding && !sortedVisible.some((f) => f.id === focusFinding.id)
        ? [focusFinding, ...sortedVisible]
        : sortedVisible,
    [focusFinding, sortedVisible]
  );

  // Kimlik kümeleri: aynı kişiye bağlı hesaplar tek başlık altında toplanır.
  const findingGroups = useMemo(() => {
    const clusters = new Map<
      string,
      { cluster: NonNullable<FindingWithAuth["cluster"]>; items: FindingWithAuth[] }
    >();
    const singles: FindingWithAuth[] = [];
    for (const finding of shown) {
      const cluster = finding.cluster;
      if (cluster && cluster.size > 1) {
        const existing = clusters.get(cluster.id);
        if (existing) existing.items.push(finding);
        else clusters.set(cluster.id, { cluster, items: [finding] });
      } else {
        singles.push(finding);
      }
    }
    return {
      clusters: [...clusters.values()].sort(
        (a, b) =>
          b.cluster.size - a.cluster.size ||
          b.cluster.max_confidence - a.cluster.max_confidence ||
          a.cluster.id.localeCompare(b.cluster.id)
      ),
      singles,
    };
  }, [shown]);

  useEffect(() => {
    if (!focusFinding) return;
    const el = document.getElementById(`finding-${focusFinding.id}`);
    if (el) {
      el.scrollIntoView({ block: "center" });
      el.focus({ preventScroll: true });
    }
  }, [focusFinding]);

  if (err)
    return (
      <p role="alert" className="rounded-[6px] border border-danger-line bg-danger-soft px-4 py-3 text-[13px] text-danger">
        {err}
      </p>
    );

  const running = scan?.status === "running" || scan?.status === "queued";

  const emptyMessage =
    findings.length === 0
      ? "Bulgular için önce bir tarama çalıştırın."
      : statusFiltered.length === 0
        ? filter === "rejected"
          ? "Reddedilen bulgu yok."
          : hiddenRejected > 0 && filter === "all"
            ? `${hiddenRejected} reddedilen bulgu gizlendi. Düşük güvenli sonuçlar varsayılan olarak gösterilmez.`
            : "Bu filtreye uyan bulgu yok."
        : narrowed.length === 0
          ? "Bu arama veya platform filtresine uyan bulgu yok."
          : hiddenLowAuth > 0
            ? `${hiddenLowAuth} düşük güvenilirlikli bulgu gizlendi.`
            : "Bu filtreye uyan bulgu yok.";

  const downloadFindings = (format: "csv" | "json") => {
    const stamp = new Date().toLocaleDateString("sv-SE");
    const base = `showis-findings-${caseId.slice(0, 8)}-${stamp}`;
    const payload = shown.map((finding) => ({
      platform: finding.platform,
      label: finding.label,
      url: finding.url ?? "",
      status: finding.status,
      score: finding.score,
      authenticity: finding.authenticity ?? null,
      corroboration: finding.corroboration ?? 0,
      signals: (finding.signals ?? []).join("|"),
      evidence_count: finding.evidence_count ?? finding.evidence_ids.length,
      cluster_id: finding.cluster_id ?? "",
      cluster_size: finding.cluster?.size ?? 1,
      cluster_reason: finding.cluster?.reasons?.[0] ?? "",
      verified_at: finding.verified_at ?? "",
      explanation: finding.explanation ?? "",
    }));

    let blob: Blob;
    if (format === "json") {
      blob = new Blob(
        [
          JSON.stringify(
            {
              exported_at: new Date().toISOString(),
              case_id: caseId,
              filters: { status: filter, platforms: platformFilter, query: findingQuery, hideLowAuth, showRejected },
              count: payload.length,
              findings: payload,
            },
            null,
            2
          ),
        ],
        { type: "application/json" }
      );
    } else {
      const headers = Object.keys(payload[0] ?? {
        platform: "",
        label: "",
        url: "",
        status: "",
        score: 0,
        authenticity: null,
        corroboration: 0,
        signals: "",
        evidence_count: 0,
        cluster_id: "",
        cluster_size: 1,
        cluster_reason: "",
        verified_at: "",
        explanation: "",
      });
      const escapeCell = (value: unknown) => `"${String(value ?? "").replace(/"/g, '""')}"`;
      const lines = [
        headers.join(","),
        ...payload.map((row) =>
          headers.map((key) => escapeCell((row as Record<string, unknown>)[key])).join(",")
        ),
      ];
      // BOM: Excel'in UTF-8'i doğru açması için.
      blob = new Blob(["\uFEFF" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
    }

    const anchor = document.createElement("a");
    anchor.href = URL.createObjectURL(blob);
    anchor.download = `${base}.${format}`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(anchor.href), 2000);
  };

  const renderFinding = (finding: FindingWithAuth) => (
    <FindingRecord
      key={finding.id}
      finding={finding}
      evidence={evidence[finding.id]}
      expanded={expanded === finding.id}
      highlighted={focusFinding?.id === finding.id}
      busy={verifying === finding.id}
      capturing={capturing === finding.id}
      onToggleEvidence={toggleEvidence}
      onVerify={verify}
      onCapture={captureScreenshot}
    />
  );

  const clusterCount = new Set(
    findings.filter((f) => (f.cluster?.size ?? 1) > 1).map((f) => f.cluster_id)
  ).size;
  const platformCount = new Set(findings.map((f) => f.platform)).size;
  const evidenceTotal = new Set(findings.flatMap((f) => f.evidence_ids ?? [])).size;

  return (
    <div className="space-y-4">
      <p className="flex flex-wrap items-center gap-x-5 gap-y-1 text-[12px] text-ink-3">
        <span>
          <span className="numeric font-medium text-ink">{findings.length}</span> bulgu
        </span>
        <span>
          <span className="numeric font-medium text-ink">{clusterCount}</span> kimlik kümesi
        </span>
        <span>
          <span className="numeric font-medium text-ink">{platformCount}</span> platform
        </span>
        <span>
          <span className="numeric font-medium text-ink">{counts.confirmed}</span> onaylı
        </span>
        <span>
          <span className="numeric font-medium text-ink">{evidenceTotal}</span> kanıt
        </span>
      </p>

      {/* Filtreler */}
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-b border-line pb-3">
        <div
          role="tablist"
          aria-label="Bulgu durumuna göre filtrele"
          className="flex flex-wrap items-center gap-x-5 gap-y-1"
        >
          {FINDING_FILTERS.map((f, i) => {
            const active = filter === f.value;
            const n = f.value === "all" ? counts.all - hiddenRejected : counts[f.value];
            return (
              <button
                key={f.value}
                id={`findings-tab-${f.value}`}
                ref={(el) => {
                  tabRefs.current[i] = el;
                }}
                type="button"
                role="tab"
                aria-selected={active}
                aria-controls="findings-panel"
                tabIndex={active ? 0 : -1}
                title={f.hint}
                onClick={() => applyFilter(f.value)}
                onKeyDown={(e) => onTabKeyDown(e, i)}
                className={`relative flex items-center gap-1.5 pb-2 pt-1 text-[13px] transition-colors ${
                  active
                    ? "font-medium text-ink after:absolute after:inset-x-0 after:bottom-[-1px] after:h-[2px] after:bg-accent"
                    : "text-ink-3 hover:text-ink"
                }`}
              >
                {f.label}
                <span className="numeric text-ink-3">{n}</span>
              </button>
            );
          })}
        </div>

        <div className="flex flex-wrap items-center gap-4">
          <button
            type="button"
            aria-pressed={hideLowAuth}
            onClick={() => applyHideLowAuth(!hideLowAuth)}
            title={
              hideLowAuth
                ? "Düşük güvenilirlikli hesapları tekrar göster"
                : "Güvenilirliği 0.35 altındaki hesapları gizle"
            }
            className={`inline-flex items-center gap-1.5 text-[12px] transition-colors ${
              hideLowAuth ? "text-danger" : "text-ink-3 hover:text-ink"
            }`}
          >
            {hideLowAuth ? <Eye size={12} aria-hidden /> : <EyeOff size={12} aria-hidden />}
            Düşük güvenilirliği gizle
            <span className="numeric text-ink-3">{lowAuthCount}</span>
          </button>
          <button
            type="button"
            aria-pressed={showRejected}
            onClick={() => applyShowRejected(!showRejected)}
            title={
              showRejected
                ? "Reddedilen bulguları tekrar gizle"
                : "Reddedilen bulguları listeye ekle"
            }
            className={`inline-flex items-center gap-1.5 text-[12px] transition-colors ${
              showRejected ? "text-warn" : "text-ink-3 hover:text-ink"
            }`}
          >
            {showRejected ? <Eye size={12} aria-hidden /> : <EyeOff size={12} aria-hidden />}
            Reddedilenleri göster
            <span className="numeric text-ink-3">{counts.rejected}</span>
          </button>
          {running && (
            <span className="status text-accent-ink">
              <span className="pulse-live h-1.5 w-1.5 rounded-full bg-accent" aria-hidden />
              tarama sürüyor
            </span>
          )}
        </div>
      </div>

      {/* OSINT daraltma: platform, etiket/URL araması, sıralama, küme görünümü */}
      <div className="flex flex-col gap-3 border-b border-line pb-3 lg:flex-row lg:items-center">
        <div className="relative min-w-[220px] flex-1">
          <Search
            size={14}
            aria-hidden
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-3"
          />
          <input
            type="search"
            value={findingQuery}
            onChange={(e) => setFindingQuery(e.target.value)}
            placeholder="Bulgu etiketi, URL veya platform ara"
            aria-label="Bulgularda ara"
            className="input py-1.5 pl-9 text-[12.5px]"
          />
          {findingQuery && (
            <button
              type="button"
              onClick={() => setFindingQuery("")}
              aria-label="Bulgu aramasını temizle"
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-ink-3 hover:text-ink"
            >
              <X size={12} aria-hidden />
            </button>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <span className="text-[12px] text-ink-3">Platform</span>
          {platformOptions.slice(0, 8).map(([platform, count]) => {
            const active = platformFilter.length === 0 || platformFilter.includes(platform);
            return (
              <button
                key={platform}
                type="button"
                aria-pressed={platformFilter.includes(platform)}
                aria-label={`${platformMeta(platform).label} platform filtresi`}
                onClick={() =>
                  setPlatformFilter((current) =>
                    current.includes(platform)
                      ? current.filter((item) => item !== platform)
                      : [...current, platform]
                  )
                }
                className={`inline-flex items-center gap-1.5 text-[12px] transition-colors ${
                  active ? "text-ink-2" : "text-ink-3 line-through"
                }`}
              >
                <span
                  className="h-2 w-2 shrink-0 rounded-[1px]"
                  style={{ backgroundColor: platformMeta(platform).accent }}
                  aria-hidden
                />
                {platformMeta(platform).label}
                <span className="numeric text-ink-3">{count}</span>
              </button>
            );
          })}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-[12px] text-ink-3">
            Sırala
            <select
              value={findingSort}
              onChange={(e) => setFindingSort(e.target.value as FindingSort)}
              aria-label="Bulgu sıralaması"
              title={FINDING_SORTS.find((item) => item.value === findingSort)?.hint}
              className="select w-auto py-1 text-[12px]"
            >
              {FINDING_SORTS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            aria-pressed={grouped}
            onClick={() => setGrouped((value) => !value)}
            title="Aynı kişiye bağlı hesapları tek kümede topla"
            className={`inline-flex items-center gap-1.5 text-[12px] transition-colors ${
              grouped ? "font-medium text-ink" : "text-ink-3 hover:text-ink"
            }`}
          >
            <Layers size={12} aria-hidden />
            Kümeler
          </button>
          <button
            type="button"
            onClick={() => downloadFindings("csv")}
            disabled={shown.length === 0}
            title="Filtrelenmiş bulguları CSV olarak indir"
            className="inline-flex items-center gap-1.5 text-[12px] text-ink-3 transition-colors hover:text-ink disabled:opacity-50"
          >
            <Download size={12} aria-hidden />
            CSV
          </button>
          <button
            type="button"
            onClick={() => downloadFindings("json")}
            disabled={shown.length === 0}
            title="Filtrelenmiş bulguları JSON olarak indir"
            className="inline-flex items-center gap-1.5 text-[12px] text-ink-3 transition-colors hover:text-ink disabled:opacity-50"
          >
            <Download size={12} aria-hidden />
            JSON
          </button>
          {(platformFilter.length > 0 || findingQuery) && (
            <button
              type="button"
              onClick={() => {
                setPlatformFilter([]);
                setFindingQuery("");
              }}
              className="btn-quiet btn-sm"
            >
              <X size={12} aria-hidden />
              Aramayı/filtreyi temizle
            </button>
          )}
        </div>
      </div>

      {actionError && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-2 rounded-[6px] border border-danger-line bg-danger-soft px-3 py-2 text-[12.5px] text-danger"
        >
          <span className="inline-flex items-center gap-2" title={actionError.detail}>
            <AlertTriangle size={13} aria-hidden />
            {actionError.message}
          </span>
          <button
            type="button"
            onClick={() => setActionError(null)}
            className="btn-quiet btn-sm"
          >
            <X size={12} aria-hidden />
            Kapat
          </button>
        </div>
      )}

      {focusEntity && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-[6px] border border-accent-line bg-accent-soft px-3 py-2 text-[12.5px] text-accent-ink">
          <span>
            {focusFinding ? (
              <>
                Grafikten gelen bulgu vurgulanıyor:{" "}
                <span className="font-medium">{focusFinding.label}</span>
              </>
            ) : (
              <>
                Grafikten gelen entity için bulgu bulunamadı:{" "}
                <span className="numeric">{focusEntity}</span>
              </>
            )}
          </span>
          <button type="button" onClick={clearFocus} className="btn-quiet btn-sm">
            <X size={12} aria-hidden />
            Vurguyu kaldır
          </button>
        </div>
      )}

      {clusterFilter && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-[6px] border border-accent-line bg-accent-soft px-3 py-2 text-[12.5px] text-accent-ink">
          <span>
            Kimlik kümesi filtresi:{" "}
            <span className="font-medium">
              {activeCluster ? `${activeCluster.size} hesap` : clusterFilter}
            </span>
            {activeCluster?.reasons?.[0] && (
              <span className="ml-2 text-ink-3">{activeCluster.reasons[0]}</span>
            )}
          </span>
          <div className="flex items-center gap-2">
            <Link
              href={`/cases/${caseId}/graph?cluster=${encodeURIComponent(clusterFilter)}`}
              className="btn-quiet btn-sm"
            >
              <ArrowUpRight size={12} aria-hidden />
              Grafikte göster
            </Link>
            <button type="button" onClick={clearClusterFilter} className="btn-quiet btn-sm">
              <X size={12} aria-hidden />
              Kümeyi temizle
            </button>
          </div>
        </div>
      )}

      {hiddenLowAuth > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-[6px] border border-line bg-sunken px-3 py-2 text-[12.5px] text-ink-3">
          <span>
            <span className="numeric font-medium text-ink-2">{hiddenLowAuth}</span> düşük
            güvenilirlikli hesap gizlendi (güvenilirlik 0.35 altı)
          </span>
          <button
            type="button"
            onClick={() => applyHideLowAuth(false)}
            className="btn-secondary btn-sm"
          >
            <Eye size={12} aria-hidden />
            Göster
          </button>
        </div>
      )}

      {hiddenRejected > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-[6px] border border-line bg-sunken px-3 py-2 text-[12.5px] text-ink-3">
          <span>
            <span className="numeric font-medium text-ink-2">{hiddenRejected}</span> reddedilen bulgu
            gizlendi (yalnızca zayıf sinyal içeren sonuçlar)
          </span>
          <button
            type="button"
            onClick={() => applyShowRejected(true)}
            className="btn-secondary btn-sm"
          >
            <Eye size={12} aria-hidden />
            Göster
          </button>
        </div>
      )}

      {/* Kayıt listesi: ayrı kartlar değil, tek bir sicil */}
      <div id="findings-panel" role="tabpanel" aria-labelledby={`findings-tab-${filter}`}>
        {shown.length === 0 ? (
          <div className="panel px-6 py-14 text-center">
            <p className="text-[13.5px] text-ink-2">{emptyMessage}</p>
            {hiddenLowAuth > 0 && (
              <button
                type="button"
                onClick={() => applyHideLowAuth(false)}
                className="btn-secondary btn-sm mt-4"
              >
                <Eye size={12} aria-hidden />
                {hiddenLowAuth} düşük güvenilirlikli bulguyu göster
              </button>
            )}
            {hiddenRejected > 0 && (
              <button
                type="button"
                onClick={() => applyShowRejected(true)}
                className="btn-secondary btn-sm mt-4"
              >
                <Eye size={12} aria-hidden />
                {hiddenRejected} reddedilen bulguyu göster
              </button>
            )}
          </div>
        ) : grouped && findingGroups.clusters.length > 0 ? (
          <div className="space-y-4">
            {findingGroups.clusters.map(({ cluster, items }) => (
              <section key={cluster.id} className="panel overflow-hidden">
                <header className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-b border-line bg-sunken px-4 py-2.5">
                  <span className="inline-flex items-center gap-1.5 text-[12.5px] font-semibold text-ink">
                    <Layers size={13} aria-hidden />
                    Kimlik kümesi
                  </span>
                  <span className="numeric text-[12px] text-ink-3">
                    {items.length}/{cluster.size} hesap
                  </span>
                  <span className="inline-flex items-center gap-1.5 text-[12px] text-ink-3">
                    <span
                      className="h-1.5 w-1.5 rounded-full bg-accent"
                      aria-hidden
                    />
                    küme güveni{" "}
                    <span className="numeric font-medium text-ink-2">
                      {Math.round(cluster.max_confidence * 100)}%
                    </span>
                  </span>
                  <span className="text-[12px] text-ink-3">
                    {cluster.evidence_count} kanıt
                  </span>
                  <span className="flex flex-wrap items-center gap-2 text-[11.5px] text-ink-3">
                    {cluster.platforms.slice(0, 6).map((platform) => (
                      <span key={platform} className="inline-flex items-center gap-1">
                        <span
                          className="h-1.5 w-1.5 rounded-full"
                          style={{ backgroundColor: platformMeta(platform).accent }}
                          aria-hidden
                        />
                        {platformMeta(platform).label}
                      </span>
                    ))}
                    {cluster.platforms.length > 6 && (
                      <span className="numeric">+{cluster.platforms.length - 6}</span>
                    )}
                  </span>
                  {cluster.reasons?.[0] && (
                    <span
                      className="max-w-[320px] truncate text-[11.5px] text-ink-3"
                      title={cluster.reasons.join(" · ")}
                    >
                      {cluster.reasons[0]}
                    </span>
                  )}
                  <button
                    type="button"
                    aria-expanded={!collapsedClusters.includes(cluster.id)}
                    aria-label={`${cluster.size} hesaplı kimlik kümesini ${
                      collapsedClusters.includes(cluster.id) ? "aç" : "kapat"
                    }`}
                    onClick={() =>
                      setCollapsedClusters((current) =>
                        current.includes(cluster.id)
                          ? current.filter((id) => id !== cluster.id)
                          : [...current, cluster.id]
                      )
                    }
                    className="ml-auto rounded p-1 text-ink-3 transition-colors hover:text-ink"
                  >
                    <ChevronDown
                      size={13}
                      aria-hidden
                      className={
                        collapsedClusters.includes(cluster.id) ? "-rotate-90" : ""
                      }
                    />
                  </button>
                  <Link
                    href={`/cases/${caseId}/graph?cluster=${encodeURIComponent(cluster.id)}`}
                    className="text-[12px] text-accent-ink hover:underline"
                  >
                    Grafikte aç
                  </Link>
                </header>
                <div
                  className={`divide-y divide-line ${
                    collapsedClusters.includes(cluster.id) ? "hidden" : ""
                  }`}
                >
                  {items.map(renderFinding)}
                </div>
              </section>
            ))}
            {findingGroups.singles.length > 0 && (
              <section className="panel overflow-hidden">
                <header className="border-b border-line bg-sunken px-4 py-2.5">
                  <span className="text-[12.5px] font-semibold text-ink">
                    Bağımsız kayıtlar
                  </span>
                  <span className="numeric ml-2 text-[12px] text-ink-3">
                    {findingGroups.singles.length}
                  </span>
                </header>
                <div className="divide-y divide-line">
                  {findingGroups.singles.map(renderFinding)}
                </div>
              </section>
            )}
          </div>
        ) : (
          <div className="panel divide-y divide-line">{shown.map(renderFinding)}</div>
        )}
      </div>
    </div>
  );
}

/* ---------------- Rapor sekmesi ---------------- */

/** Rapor notları backend'de markdown vurgusu içerebilir (`**...**`) — düz metne indir. */
function plainNote(value: string | null | undefined): string {
  return (value ?? "").replace(/\*\*/g, "").trim();
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("tr-TR");
}

const REPORT_STATUS_ORDER = ["confirmed", "candidate", "lead", "rejected"];

/**
 * T-313 rapor bölümleri. Eski raporlarda alanlar yoktur → `null` döner ve
 * ilgili bölüm hiç render edilmez (sessiz geriye uyum).
 */
function reportSections(data: CaseReportJson | null) {
  const platforms = data?.platform_distribution ?? data?.platform_breakdown ?? null;
  return {
    summary: data?.executive_summary ?? null,
    platforms: Array.isArray(platforms) && platforms.length > 0 ? platforms : null,
    identity: data?.identity_links ?? null,
    timeline: Array.isArray(data?.timeline) && data.timeline.length > 0 ? data.timeline : null,
    evidence: data?.evidence_summary ?? null,
  };
}

function ReportSummary({ data }: { data: CaseReportJson }) {
  const { summary, platforms, identity, timeline, evidence } = reportSections(data);
  const sharedGroups = identity?.shared_username_groups ?? [];
  const identityEdges = identity?.edges ?? [];
  const topFindings = summary?.top_findings ?? [];
  const totals = summary?.totals ?? {};
  const statusCounts = summary?.status_counts ?? {};
  const statusEntries = REPORT_STATUS_ORDER.map((status) => ({
    status,
    count: Number(statusCounts[status] ?? 0),
  })).filter((row, index, rows) => row.count > 0 || index < rows.length);
  const otherStatuses = Object.entries(statusCounts).filter(
    ([status]) => !REPORT_STATUS_ORDER.includes(status)
  );

  return (
    <div className="space-y-5">
      {summary && (
        <section className="panel">
          <div className="panel-head">
            <h2 className="panel-title">Yönetici özeti</h2>
            <span className="numeric text-ink-3">
              oluşturuldu {formatDateTime(summary.generated_at)}
            </span>
          </div>

          <div className="p-5">
            <dl className="grid gap-x-8 gap-y-2 text-[12.5px] sm:grid-cols-2">
              <div className="flex gap-2">
                <dt className="w-20 shrink-0 text-ink-3">Hedef</dt>
                <dd className="text-ink">{summary.target_name || "—"}</dd>
              </div>
              <div className="flex gap-2">
                <dt className="w-20 shrink-0 text-ink-3">Vaka</dt>
                <dd className="truncate text-ink">{summary.case_name || "—"}</dd>
              </div>
              <div className="flex gap-2">
                <dt className="w-20 shrink-0 text-ink-3">Mod</dt>
                <dd className="numeric text-ink">{summary.mode || "—"}</dd>
              </div>
              <div className="flex gap-2">
                <dt className="w-20 shrink-0 text-ink-3">Tarama</dt>
                <dd className="numeric text-ink-2">
                  {formatDateTime(summary.scan_started_at)} — {formatDateTime(summary.scan_finished_at)}
                </dd>
              </div>
            </dl>

            {/* Toplamlar: ölçü satırı, kart ızgarası değil */}
            <dl className="mt-5 grid grid-cols-2 gap-x-8 gap-y-3 border-t border-line pt-4 sm:grid-cols-4">
              {[
                { label: "Hedef kaydı", value: totals.entities },
                { label: "Platform", value: totals.platforms },
                { label: "Bulgu", value: totals.findings },
                { label: "Kanıt", value: totals.evidence },
              ].map((item) => (
                <div key={item.label}>
                  <dd className="tabular text-[20px] font-semibold leading-none text-ink">
                    {item.value ?? 0}
                  </dd>
                  <dt className="mt-1.5 text-[12px] text-ink-3">{item.label}</dt>
                </div>
              ))}
            </dl>

            {(statusEntries.length > 0 || otherStatuses.length > 0) && (
              <div className="mt-5 border-t border-line pt-4">
                <p className="section-heading">Durum dağılımı</p>
                <div className="mt-2.5 flex flex-wrap items-center gap-x-5 gap-y-2">
                  {statusEntries.map((row) => (
                    <span key={row.status} className="flex items-center gap-2">
                      <Badge value={row.status} />
                      <span className="numeric text-ink-2">{row.count}</span>
                    </span>
                  ))}
                  {otherStatuses.map(([status, count]) => (
                    <span key={status} className="flex items-center gap-2">
                      <Badge value={status} />
                      <span className="numeric text-ink-2">{count}</span>
                    </span>
                  ))}
                </div>
              </div>
            )}

            {summary.confidence_note && (
              /* Panelin içinde ikinci bir kutu olmaz: not, ayraçla ayrılmış
                 düz metindir. */
              <p className="mt-5 border-t border-line pt-4 text-[12.5px] leading-relaxed text-ink-2">
                {plainNote(summary.confidence_note)}
              </p>
            )}
          </div>

          {topFindings.length > 0 && (
            <div className="border-t border-line">
              <table className="data-table">
                <caption className="px-4 py-2.5 text-left text-[12px] font-medium text-ink-2">
                  En yüksek güvenli 5 bulgu
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Etiket</th>
                    <th scope="col">Platform</th>
                    <th scope="col">Durum</th>
                    <th scope="col" className="text-right">
                      Skor
                    </th>
                    <th scope="col" className="hidden lg:table-cell">
                      Gerekçe
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {topFindings.slice(0, 5).map((finding, index) => (
                    <tr key={`${finding.platform}-${finding.label}-${index}`}>
                      <th
                        scope="row"
                        className="max-w-[180px] truncate text-left font-medium text-ink"
                      >
                        {finding.url ? (
                          <a
                            href={finding.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="link"
                          >
                            {finding.label}
                          </a>
                        ) : (
                          finding.label
                        )}
                      </th>
                      <td className="text-ink-3">{platformMeta(finding.platform).label}</td>
                      <td>
                        <Badge value={finding.status} />
                      </td>
                      <td className="numeric text-right text-ink-2">
                        {Math.round((finding.score ?? 0) * 100)}%
                      </td>
                      <td className="hidden max-w-xs truncate text-[12px] text-ink-3 lg:table-cell">
                        {finding.reason}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        {platforms && (
          <section className="panel overflow-hidden">
            <div className="panel-head">
              <h2 className="panel-title">Platform dağılımı</h2>
            </div>
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Platform</th>
                  <th scope="col" className="text-right">
                    Bulgu
                  </th>
                  <th scope="col" className="text-right">
                    En yüksek
                  </th>
                  <th scope="col" className="text-right">
                    Ortalama
                  </th>
                </tr>
              </thead>
              <tbody>
                {platforms.slice(0, 12).map((row) => (
                  <tr key={row.platform}>
                    <th scope="row" className="text-left font-medium text-ink">
                      {platformMeta(row.platform).label}
                    </th>
                    <td className="numeric text-right text-ink-2">{row.findings}</td>
                    <td className="numeric text-right text-ink-2">
                      {Math.round((row.max_score ?? 0) * 100)}%
                    </td>
                    <td className="numeric text-right text-ink-3">
                      {Math.round((row.avg_score ?? 0) * 100)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {platforms.length > 12 && (
              <p className="border-t border-line px-4 py-2 text-[12px] text-ink-3">
                {platforms.length - 12} platform daha
              </p>
            )}
          </section>
        )}

        {(identityEdges.length > 0 || sharedGroups.length > 0 || identity?.confidence_note) && (
          <section className="panel p-5">
            <h2 className="section-heading">Kimlik ve çapraz bağlantı özeti</h2>
            {identity?.confidence_note && (
              <p className="mt-2 text-[12px] leading-relaxed text-ink-3">
                {plainNote(identity.confidence_note)}
              </p>
            )}

            {sharedGroups.length > 0 && (
              <div className="mt-4">
                <p className="text-[12px] font-medium text-ink-2">
                  Paylaşılan kullanıcı adları ({sharedGroups.length})
                </p>
                <ul className="mt-2 space-y-1">
                  {sharedGroups.slice(0, 8).map((group) => (
                    <li key={group.username} className="flex items-baseline gap-2 text-[12px]">
                      <span className="numeric text-ink">{group.username}</span>
                      <span className="text-ink-3">{group.platforms.join(", ")}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {identityEdges.length > 0 && (
              <table className="data-table mt-4">
                <caption className="sr-only">Çapraz bağlantılar</caption>
                <thead>
                  <tr>
                    <th scope="col">Tip</th>
                    <th scope="col" className="text-right">
                      Güven
                    </th>
                    <th scope="col">Gerekçe</th>
                  </tr>
                </thead>
                <tbody>
                  {identityEdges.slice(0, 8).map((edge, index) => (
                    <tr key={`${edge.source}-${edge.target}-${index}`}>
                      <th scope="row" className="text-left font-normal text-ink-2" title={edge.type}>
                        {edgeTypeLabel(edge.type)}
                      </th>
                      <td className="numeric text-right text-ink-2">
                        {Math.round((edge.confidence ?? 0) * 100)}%
                      </td>
                      <td className="max-w-xs truncate text-[12px] text-ink-3">
                        {edge.reason || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        )}
      </div>

      {timeline && (
        <section className="panel overflow-hidden">
          <div className="panel-head">
            <h2 className="panel-title">Kronoloji</h2>
            <span className="numeric text-ink-3">{timeline.length} kayıt</span>
          </div>
          <div className="scroll-x max-h-72 overflow-y-auto">
            <table className="data-table">
              <thead className="sticky top-0">
                <tr>
                  <th scope="col">Tarih</th>
                  <th scope="col">Platform</th>
                  <th scope="col">Kayıt</th>
                  <th scope="col" className="hidden sm:table-cell">
                    Olay
                  </th>
                </tr>
              </thead>
              <tbody>
                {timeline.slice(0, 30).map((row, index) => (
                  <tr key={`${row.date}-${row.label}-${index}`}>
                    <th scope="row" className="numeric whitespace-nowrap text-left font-normal text-ink-2">
                      {formatDateTime(row.date)}
                    </th>
                    <td className="text-ink-3">{platformMeta(row.platform).label}</td>
                    <td className="max-w-[220px] truncate text-ink">{row.label}</td>
                    <td className="hidden text-[12px] text-ink-3 sm:table-cell" title={row.event}>
                      {timelineEventLabel(row.event)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {timeline.length > 30 && (
            <p className="border-t border-line px-4 py-2 text-[12px] text-ink-3">
              {timeline.length - 30} kayıt daha
            </p>
          )}
        </section>
      )}

      {evidence && (
        <section className="panel p-5">
          <h2 className="section-heading">Kanıt özeti</h2>
          <dl className="mt-3 grid grid-cols-2 gap-x-8 gap-y-3 sm:grid-cols-3">
            <div>
              <dd className="tabular text-[20px] font-semibold leading-none text-ink">
                {evidence.total ?? 0}
              </dd>
              <dt className="mt-1.5 text-[12px] text-ink-3">Toplam kanıt</dt>
            </div>
            <div>
              <dd className="tabular text-[20px] font-semibold leading-none text-ink">
                {evidence.linked_to_entities ?? 0}
              </dd>
              <dt className="mt-1.5 text-[12px] text-ink-3">Hedefe bağlı</dt>
            </div>
            <div>
              <dd className="numeric text-[13px] font-medium text-ink">
                {formatDateTime(evidence.retention_until)}
              </dd>
              <dt className="mt-1.5 text-[12px] text-ink-3">Saklama bitişi</dt>
            </div>
          </dl>

          {evidence.by_entity && evidence.by_entity.length > 0 && (
            <table className="data-table mt-4">
              <caption className="pb-1 text-left text-[12px] font-medium text-ink-2">
                En çok kanıtı olan hedef kayıtları
              </caption>
              <thead>
                <tr>
                  <th scope="col">Kayıt</th>
                  <th scope="col">Platform</th>
                  <th scope="col" className="text-right">
                    Kanıt
                  </th>
                </tr>
              </thead>
              <tbody>
                {evidence.by_entity.slice(0, 10).map((row) => (
                  <tr key={row.entity_id}>
                    <th scope="row" className="max-w-[240px] truncate text-left font-normal text-ink">
                      {row.label}
                    </th>
                    <td className="text-ink-3">{platformMeta(row.platform).label}</td>
                    <td className="numeric text-right text-ink-2">{row.evidence}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
    </div>
  );
}

export function ReportTab({ caseId }: { caseId: string }) {
  const [reports, setReports] = useState<ReportInfo[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [summary, setSummary] = useState<CaseReportJson | null>(null);
  const [summaryBusy, setSummaryBusy] = useState(false);
  const [summaryErr, setSummaryErr] = useState<string | null>(null);
  const summaryKey = `showis:report:summary:${caseId}`;

  // Yönetici özeti: JSON raporu üretilir, indirme adresinden içerik çekilir.
  // (Listeleme ucu olmadığı için tek yol rapor üretmek; oturumda bir kez yapılır.)
  const loadSummary = useCallback(async () => {
    setSummaryBusy(true);
    setSummaryErr(null);
    try {
      const report = await api.createReport(caseId, "json");
      setReports((prev) => [report, ...prev.filter((r) => r.id !== report.id)]);
      const res = await fetch(report.download_url, { headers: { Accept: "application/json" } });
      if (!res.ok) throw new Error(`Rapor içeriği alınamadı (HTTP ${res.status})`);
      const data = (await res.json()) as CaseReportJson;
      setSummary(data);
      try {
        window.sessionStorage.setItem(summaryKey, JSON.stringify(data));
      } catch {
        /* kota dolu olabilir: önbellek atlanır, işlev görmezden gelir */
      }
    } catch (e: any) {
      setSummaryErr(e?.message ?? "Yönetici özeti yüklenemedi");
    } finally {
      setSummaryBusy(false);
    }
  }, [caseId, summaryKey]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const cached = window.sessionStorage.getItem(summaryKey);
    if (cached) {
      try {
        setSummary(JSON.parse(cached) as CaseReportJson);
        return;
      } catch {
        window.sessionStorage.removeItem(summaryKey);
      }
    }
    void loadSummary();
  }, [loadSummary, summaryKey]);

  const generate = async (format: "json" | "pdf" | "html") => {
    setBusy(format);
    setErr(null);
    try {
      const r = await api.createReport(caseId, format);
      setReports((prev) => [r, ...prev]);
      if (format === "html") {
        // HTML raporu sayfa içinde RENDER ETME (XSS yüzeyi açmamak için):
        // yalnız indir. API `Content-Disposition: attachment` + sıkı CSP gönderir;
        // ayrıca `download` özniteliğiyle yeni sekme hiç açılmaz.
        const anchor = document.createElement("a");
        anchor.href = r.download_url;
        anchor.download = "";
        anchor.rel = "noopener";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
      } else {
        window.open(r.download_url, "_blank");
      }
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(null);
    }
  };

  const downloadStix = async () => {
    setBusy("stix");
    setErr(null);
    try {
      const bundle = await api.getStix(caseId);
      const blob = new Blob([JSON.stringify(bundle, null, 2)], {
        type: "application/stix+json;version=2.1",
      });
      const anchor = document.createElement("a");
      anchor.href = URL.createObjectURL(blob);
      anchor.download = `showis-stix-${caseId.slice(0, 8)}.json`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(anchor.href), 2000);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(null);
    }
  };

  const downloadMisp = async () => {
    setBusy("misp");
    setErr(null);
    try {
      const event = await api.getMisp(caseId);
      const blob = new Blob([JSON.stringify(event, null, 2)], {
        type: "application/json",
      });
      const anchor = document.createElement("a");
      anchor.href = URL.createObjectURL(blob);
      anchor.download = `showis-misp-${caseId.slice(0, 8)}.json`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(anchor.href), 2000);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(null);
    }
  };

  const sections = reportSections(summary);
  const hasSections = Boolean(
    sections.summary || sections.platforms || sections.identity || sections.timeline || sections.evidence
  );

  return (
    <div className="space-y-5">
      <section className="panel max-w-2xl p-5">
        <h2 className="section-heading">Rapor oluştur</h2>
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink-3">
          Rapor; vaka bilgilerini, bulguları, skor kırılımlarını ve kanıt metaverilerini içerir.
        </p>

        <div className="mt-4 flex flex-wrap gap-2">
          <button onClick={() => generate("pdf")} disabled={busy !== null} className="btn-primary">
            {busy === "pdf" ? "Oluşturuluyor" : "PDF rapor"}
          </button>
          <button onClick={() => generate("json")} disabled={busy !== null} className="btn-secondary">
            {busy === "json" ? "Oluşturuluyor" : "JSON rapor"}
          </button>
          <button
            type="button"
            onClick={() => generate("html")}
            disabled={busy !== null}
            className="btn-secondary"
            aria-label="HTML raporunu indir (sayfada açılmaz)"
            title="HTML raporunu indirir; tarayıcıda açmaz"
          >
            {busy === "html" ? (
              "Oluşturuluyor"
            ) : (
              <>
                <FileCode2 size={13} aria-hidden />
                HTML indir
              </>
            )}
          </button>
          <button
            type="button"
            onClick={downloadStix}
            disabled={busy !== null}
            className="btn-secondary"
            title="STIX 2.1 bundle indir (MISP/OpenCTI)"
          >
            {busy === "stix" ? "Oluşturuluyor" : "STIX"}
          </button>
          <button
            type="button"
            onClick={downloadMisp}
            disabled={busy !== null}
            className="btn-secondary"
            title="MISP Event JSON indir"
          >
            {busy === "misp" ? "Oluşturuluyor" : "MISP"}
          </button>
          <button
            type="button"
            onClick={loadSummary}
            disabled={summaryBusy}
            className="btn-quiet"
            aria-label="Yönetici özetini yenile"
          >
            <RefreshCw size={13} className={summaryBusy ? "animate-spin" : ""} aria-hidden />
            Özeti yenile
          </button>
        </div>

        {err && (
          <p role="alert" className="mt-4 text-[12.5px] text-danger">
            {err}
          </p>
        )}

        {reports.length > 0 && (
          <div className="mt-5 border-t border-line pt-4">
            <p className="text-[12px] font-medium text-ink-2">Son oluşturulanlar</p>
            <ul className="mt-2 space-y-1">
              {reports.map((r) => (
                <li key={r.id}>
                  <a href={r.download_url} className="link text-[12.5px]">
                    {r.format.toUpperCase()} —{" "}
                    {r.created_at ? new Date(r.created_at).toLocaleString("tr-TR") : r.id}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {!summary && summaryBusy && (
        <div className="panel flex items-center gap-2 px-5 py-6 text-[13px] text-ink-3">
          <Loader2 size={14} className="animate-spin" aria-hidden />
          Yönetici özeti hazırlanıyor
        </div>
      )}

      {!summary && !summaryBusy && summaryErr && (
        <p className="rounded-[6px] border border-warn-line bg-warn-soft px-4 py-3 text-[13px] text-warn">
          Yönetici özeti yüklenemedi: {summaryErr}
        </p>
      )}

      {summary && hasSections && <ReportSummary data={summary} />}
    </div>
  );
}

/* ---------------- Yorumlar sekmesi ---------------- */

export function CommentsTab({ caseId }: { caseId: string }) {
  const [comments, setComments] = useState<CaseComment[]>([]);
  const [body, setBody] = useState("");
  const [author, setAuthor] = useState("operator");
  const [owner, setOwner] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getComments(caseId)
      .then(setComments)
      .catch((e: Error) => setError(e.message));
    api
      .getCase(caseId)
      .then((data) => setOwner(data.owner ?? ""))
      .catch(() => {});
  }, [caseId]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const text = body.trim();
    if (!text) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.addComment(caseId, text, author.trim() || "operator");
      setComments((prev) => [...prev, created]);
      setBody("");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const saveOwner = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.updateOwner(caseId, owner.trim() || null);
      setOwner(result.owner ?? "");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <section className="panel p-5">
        <h2 className="section-heading">Vaka sahibi</h2>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label className="min-w-[220px] flex-1">
            <span className="label">Operasyonel sahip</span>
            <input
              className="input"
              value={owner}
              onChange={(e) => setOwner(e.target.value)}
              placeholder="ör. analist@example"
            />
          </label>
          <button type="button" onClick={saveOwner} disabled={busy} className="btn-secondary">
            Kaydet
          </button>
        </div>
        <p className="mt-2 text-[11.5px] text-ink-3">
          Sahip alanı operasyonel bir etikettir; tek paylaşılan API token’ı nedeniyle
          yetkilendirme sınırı değildir.
        </p>
      </section>

      <section className="panel overflow-hidden">
        <header className="border-b border-line bg-sunken px-4 py-2.5">
          <span className="text-[12.5px] font-semibold text-ink">Yorumlar</span>
          <span className="numeric ml-2 text-[12px] text-ink-3">{comments.length}</span>
        </header>
        {comments.length === 0 ? (
          <p className="px-4 py-4 text-[12.5px] text-ink-3">Henüz yorum yok.</p>
        ) : (
          <ul className="divide-y divide-line">
            {comments.map((comment) => (
              <li key={comment.id} className="px-4 py-3">
                <div className="flex flex-wrap items-center gap-x-3 text-[12px] text-ink-3">
                  <span className="font-medium text-ink-2">{comment.author}</span>
                  {comment.created_at && (
                    <span className="numeric">
                      {new Date(comment.created_at).toLocaleString("tr-TR")}
                    </span>
                  )}
                </div>
                <p className="mt-1 whitespace-pre-wrap text-[13px] text-ink-2">{comment.body}</p>
              </li>
            ))}
          </ul>
        )}
        <form onSubmit={submit} className="space-y-2 border-t border-line p-4">
          <div className="flex flex-wrap gap-2">
            <input
              className="input w-44"
              value={author}
              onChange={(e) => setAuthor(e.target.value)}
              aria-label="Yorum yazarı"
              placeholder="Yazar"
            />
            <span className="text-[11.5px] text-ink-3 self-center">
              X-Actor başlığı varsa o kullanılır
            </span>
          </div>
          <textarea
            className="input min-h-[90px] w-full resize-y"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Vaka notu veya inceleme yorumu"
            maxLength={4000}
          />
          {error && <p className="text-[12px] text-danger">{error}</p>}
          <button type="submit" disabled={busy || !body.trim()} className="btn-primary btn-sm">
            {busy ? "Kaydediliyor" : "Yorum ekle"}
          </button>
        </form>
      </section>
    </div>
  );
}

/* ---------------- Değişim sekmesi ---------------- */

export function DiffTab({ caseId }: { caseId: string }) {
  const [diff, setDiff] = useState<CaseDiff | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getDiff(caseId).then(setDiff).catch((e) => setError(e.message));
  }, [caseId]);

  if (error)
    return (
      <p role="alert" className="rounded-[6px] border border-danger-line bg-danger-soft px-4 py-3 text-[13px] text-danger">
        Değişim yüklenemedi: {error}
      </p>
    );
  if (!diff)
    return (
      <p className="panel flex items-center gap-2 px-5 py-6 text-[13px] text-ink-3">
        <Loader2 size={14} className="animate-spin" aria-hidden />
        Değişim hesaplanıyor
      </p>
    );
  if (!diff.available)
    return (
      <div className="panel px-5 py-8 text-[13px] text-ink-3">
        Karşılaştırılabilir iki tarama anlık görüntüsü yok.
        <p className="mt-1 text-[12px]">
          En az iki tamamlanmış tarama sonrası bu sekme yeni, çıkan ve değişen
          bulguları gösterir.
        </p>
      </div>
    );

  const findings = diff.findings ?? { new: [], removed: [], changed: [] };
  const counts = diff.counts ?? {};
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        {[
          ["Yeni bulgu", counts.findings_new ?? 0],
          ["Çıkan bulgu", counts.findings_removed ?? 0],
          ["Değişen", counts.findings_changed ?? 0],
          ["Yeni entity", counts.entities_added ?? 0],
          ["Yeni kenar", counts.edges_added ?? 0],
        ].map(([label, value]) => (
          <div key={String(label)} className="panel px-3 py-3">
            <p className="text-[11.5px] text-ink-3">{label}</p>
            <p className="numeric mt-1 text-[19px] font-semibold text-ink">{value}</p>
          </div>
        ))}
      </div>

      <p className="numeric text-[11.5px] text-ink-3">
        {diff.from_scan ? `Önceki: ${diff.from_scan.slice(0, 8)}` : "Önceki tarama yok"} →{" "}
        {diff.to_scan ? `Güncel: ${diff.to_scan.slice(0, 8)}` : "—"}
      </p>

      {(
        [
          ["Yeni bulgular", findings.new],
          ["Çıkan bulgular", findings.removed],
        ] as const
      ).map(([title, rows]) => (
        <section key={title} className="panel overflow-hidden">
          <header className="border-b border-line bg-sunken px-4 py-2.5">
            <span className="text-[12.5px] font-semibold text-ink">{title}</span>
            <span className="numeric ml-2 text-[12px] text-ink-3">{rows.length}</span>
          </header>
          {rows.length === 0 ? (
            <p className="px-4 py-3 text-[12px] text-ink-3">Kayıt yok.</p>
          ) : (
            <ul className="divide-y divide-line">
              {rows.map((finding) => (
                <li key={finding.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 text-[12.5px]">
                  <Link
                    href={`/cases/${caseId}/findings?entity=${encodeURIComponent(finding.entity_id)}`}
                    className="font-medium text-ink hover:text-accent-ink"
                  >
                    {finding.label}
                  </Link>
                  <PlatformBadge platform={finding.platform} />
                  <Badge value={finding.status} />
                  <span className="numeric ml-auto text-ink-3">
                    {Math.round(finding.score * 100)}%
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}

      <section className="panel overflow-hidden">
        <header className="border-b border-line bg-sunken px-4 py-2.5">
          <span className="text-[12.5px] font-semibold text-ink">Durum/skor değişenler</span>
          <span className="numeric ml-2 text-[12px] text-ink-3">{findings.changed.length}</span>
        </header>
        {findings.changed.length === 0 ? (
          <p className="px-4 py-3 text-[12px] text-ink-3">Kayıt yok.</p>
        ) : (
          <ul className="divide-y divide-line">
            {findings.changed.map(({ id, before, after }) => (
              <li key={id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 text-[12.5px]">
                <Link
                  href={`/cases/${caseId}/findings?entity=${encodeURIComponent(after.entity_id)}`}
                  className="font-medium text-ink hover:text-accent-ink"
                >
                  {after.label}
                </Link>
                <span className="numeric text-ink-3">
                  {before.status}/{Math.round(before.score * 100)}% →{" "}
                  {after.status}/{Math.round(after.score * 100)}%
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/* ---------------- Denetim sekmesi ---------------- */

export function AuditTab({ caseId }: { caseId: string }) {
  const [logs, setLogs] = useState<AuditEntry[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.getAudit(caseId).then(setLogs).catch((e) => setErr(e.message));
  }, [caseId]);

  if (err)
    return (
      <p role="alert" className="rounded-[6px] border border-danger-line bg-danger-soft px-4 py-3 text-[13px] text-danger">
        {err}
      </p>
    );

  return (
    <div className="panel overflow-hidden">
      <div className="scroll-x">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Zaman</th>
              <th scope="col">Aktör</th>
              <th scope="col">Aksiyon</th>
              <th scope="col" className="hidden md:table-cell">
                Hedef
              </th>
              <th scope="col" className="hidden lg:table-cell">
                Detay
              </th>
            </tr>
          </thead>
          <tbody>
            {logs.map((l) => (
              <tr key={l.id}>
                <td className="numeric whitespace-nowrap text-ink-3">
                  {l.created_at ? new Date(l.created_at).toLocaleString("tr-TR") : ""}
                </td>
                <td className="text-ink-2">{l.actor || "—"}</td>
                <td className="text-ink-2">
                  <span title={l.action}>{auditActionLabel(l.action)}</span>
                </td>
                <td className="hidden max-w-[16rem] truncate font-mono text-[12px] text-ink-3 md:table-cell">
                  {l.target || "—"}
                </td>
                <td className="hidden max-w-xs truncate font-mono text-[12px] text-ink-3 lg:table-cell">
                  {l.details ? JSON.stringify(l.details) : ""}
                </td>
              </tr>
            ))}
            {logs.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-10 text-center text-ink-3">
                  Bu vaka için denetim kaydı yok.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
