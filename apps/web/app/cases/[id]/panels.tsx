"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  BarChart3,
  CalendarClock,
  Check,
  ChevronDown,
  Clock,
  ExternalLink,
  Eye,
  EyeOff,
  FileText,
  Fingerprint,
  Gauge,
  Loader2,
  Play,
  Radio,
  RefreshCw,
  ShieldCheck,
  Square,
  Users,
  X,
} from "lucide-react";
import {
  api,
  type AuditEntry,
  type EvidenceItem,
  type FindingItem,
  type ReportInfo,
} from "../../../lib/api";
import type { CaseReportJson } from "../../../lib/types";
import { platformMeta } from "./platform-meta";
import { formatDuration, useScanSession } from "./scan-context";

const STATUS_STYLE: Record<string, string> = {
  queued: "border-slate-700 bg-slate-800/70 text-slate-300",
  running: "border-blue-500/40 bg-blue-500/10 text-blue-300",
  completed: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  failed: "border-red-500/40 bg-red-500/10 text-red-300",
  cancelled: "border-amber-500/40 bg-amber-500/10 text-amber-300",
  lead: "border-amber-500/40 bg-amber-500/10 text-amber-300",
  candidate: "border-amber-500/40 bg-amber-500/10 text-amber-300",
  confirmed: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  rejected: "border-red-500/40 bg-red-500/10 text-red-300",
  done: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  error: "border-red-500/40 bg-red-500/10 text-red-300",
  pending: "border-slate-700 bg-slate-800/70 text-slate-400",
};

const STATUS_TR: Record<string, string> = {
  queued: "sırada",
  running: "çalışıyor",
  completed: "tamamlandı",
  failed: "başarısız",
  cancelled: "iptal",
  lead: "ipucu",
  candidate: "aday",
  confirmed: "onaylı",
  rejected: "reddedildi",
  done: "tamam",
  error: "hata",
  pending: "bekliyor",
  active: "aktif",
  archived: "arşiv",
};

export function Badge({ value }: { value: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${
        STATUS_STYLE[value] || "border-slate-700 bg-slate-800/70 text-slate-300"
      }`}
    >
      {STATUS_TR[value] || value}
    </span>
  );
}

/** Platform rozeti — `platform-meta.ts` etiket/renklerini kullanır, bilinmeyende nötr fallback. */
export function PlatformBadge({ platform }: { platform: string }) {
  const meta = platformMeta(platform);
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium text-slate-200"
      style={{ borderColor: `${meta.accent}66`, backgroundColor: `${meta.accent}14` }}
      title={meta.label === platform ? undefined : platform}
    >
      <span
        className="h-1.5 w-1.5 shrink-0 rounded-full"
        style={{ backgroundColor: meta.accent }}
        aria-hidden
      />
      {meta.label}
    </span>
  );
}

const MODES = [
  { value: "O1", label: "O1 · Hızlı", desc: "Arama + API'ler (site-check yok)" },
  { value: "O2", label: "O2 · Standart", desc: "Arama + API + 6 kullanıcı adı site-check" },
  { value: "O3", label: "O3 · Derin", desc: "Standart + tarayıcı render, 10 kullanıcı adı" },
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
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-5">
      <div className="space-y-5 lg:col-span-3">
        <div className="card p-5">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-100">Tarama Modu</h3>
            {running && (
              <span className="chip border-blue-500/40 bg-blue-500/10 text-blue-300">
                <span className="live-dot h-1.5 w-1.5 rounded-full bg-blue-400" />
                canlı
              </span>
            )}
          </div>

          <div className="mt-3 grid gap-2 sm:grid-cols-3">
            {MODES.map((m) => {
              const active = mode === m.value;
              return (
                <button
                  key={m.value}
                  type="button"
                  disabled={running}
                  onClick={() => setMode(m.value)}
                  className={`rounded-lg border p-3 text-left transition-colors disabled:opacity-60 ${
                    active
                      ? "border-blue-500/50 bg-blue-500/10"
                      : "border-slate-800 bg-slate-950 hover:border-slate-700"
                  }`}
                >
                  <span
                    className={`block text-sm font-medium ${active ? "text-blue-200" : "text-slate-200"}`}
                  >
                    {m.label}
                  </span>
                  <span className="mt-1 block text-[11px] leading-snug text-slate-400">{m.desc}</span>
                </button>
              );
            })}
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2.5 text-xs text-slate-400">
            <span className="inline-flex items-center gap-1.5">
              <Clock size={13} className="text-blue-400" />
              Tahmini süre{" "}
              <span className="mono font-medium text-slate-200">
                ~{estimate ? formatDuration(estimate.seconds) : "hesaplanıyor"}
              </span>
            </span>
            {estimate && (
              <>
                <span className="inline-flex items-center gap-1.5">
                  <Gauge size={13} className="text-blue-400" />
                  <span className="mono text-slate-200">{estimate.tasks}</span> görev
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <Radio size={13} className="text-blue-400" />
                  ~<span className="mono text-slate-200">{estimate.requests}</span> istek
                </span>
              </>
            )}
          </div>

          {estimate && estimate.steps.length > 0 && (
            <ul className="mt-3 space-y-1 text-[11px] text-slate-500">
              {estimate.steps.map((s) => (
                <li key={s.key} className="flex items-center gap-2">
                  <span className="h-1 w-1 rounded-full bg-slate-600" />
                  {s.label}
                  <span className="mono text-slate-600">×{s.tasks}</span>
                </li>
              ))}
            </ul>
          )}

          <div className="mt-4 flex flex-wrap gap-2">
            <button onClick={start} disabled={starting || running} className="btn-primary">
              {starting ? (
                <Loader2 size={15} className="animate-spin" />
              ) : (
                <Play size={15} />
              )}
              {running ? "Tarama sürüyor" : scan ? "Yeniden tara" : "Taramayı başlat"}
            </button>
            {running && (
              <button onClick={cancel} className="btn-ghost">
                <Square size={13} /> İptal
              </button>
            )}
          </div>
          {error && (
            <p className="mt-3 flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2 text-xs text-red-300">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" /> {error}
            </p>
          )}
        </div>

        {scan && (
          <div className="card p-5">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-100">İlerleme</h3>
              <div className="flex items-center gap-2">
                <Badge value={scan.status} />
                <span className="mono text-[11px] text-slate-500">{scan.mode}</span>
              </div>
            </div>

            <div className="mt-4">
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                <div
                  className={`h-full rounded-full transition-all duration-700 ${
                    scan.status === "failed"
                      ? "bg-red-500"
                      : scan.status === "completed"
                        ? "bg-emerald-500"
                        : "bg-blue-400"
                  }`}
                  style={{ width: `${scan.status === "completed" ? 100 : progress}%` }}
                />
              </div>
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-400">
                <span>
                  Geçen <span className="mono text-slate-200">{formatDuration(elapsed)}</span>
                  {running && eta > 0 && (
                    <>
                      {" · kalan tahmini "}
                      <span className="mono text-slate-200">{formatDuration(remaining)}</span>
                    </>
                  )}
                </span>
                <span>
                  <span className="mono text-slate-200">{scan.tasks_count}</span> görev ·{" "}
                  <span className="mono text-slate-200">
                    {scan.tasks.filter((t) => t.status === "done").length}
                  </span>{" "}
                  tamam
                </span>
              </div>
            </div>

            <div className="mt-4 max-h-80 space-y-1 overflow-y-auto pr-1">
              {scan.tasks.map((t) => (
                <div
                  key={t.id}
                  className="flex items-center justify-between rounded-md border border-slate-800 bg-slate-950/60 px-3 py-1.5 text-xs"
                >
                  <span className="min-w-0 truncate text-slate-300">
                    <span className="font-mono text-slate-500">{t.connector}</span>
                    <span className="mx-1.5 text-slate-700">·</span>
                    {t.kind}
                    {t.error && <span className="ml-2 text-red-400">{t.error.slice(0, 60)}</span>}
                  </span>
                  <span className="flex shrink-0 items-center gap-2">
                    {t.observation_count > 0 && (
                      <span className="mono text-slate-500">{t.observation_count} gözlem</span>
                    )}
                    <Badge value={t.status} />
                  </span>
                </div>
              ))}
              {scan.tasks.length === 0 && (
                <p className="text-xs text-slate-500">
                  Görev planlanıyor… connector&apos;lar hazırlanıyor.
                </p>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="card flex flex-col p-5 lg:col-span-2">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-100">Canlı Olay Akışı</h3>
          <span className="mono text-[11px] text-slate-500">{events.length} olay</span>
        </div>
        <div className="mt-3 max-h-[560px] flex-1 space-y-1 overflow-y-auto pr-1 font-mono text-[11px]">
          {events.length === 0 && (
            <p className="font-sans text-xs text-slate-500">
              Olay yok — tarama başlatıldığında connector çıktıları burada akar.
            </p>
          )}
          {events.map((ev, i) => {
            const bad = ev.type.includes("fail") || ev.type.includes("error");
            return (
              <div key={i} className="flex gap-2 leading-relaxed">
                <span className="shrink-0 text-slate-600">{ev.at}</span>
                <span className={bad ? "shrink-0 text-red-400" : "shrink-0 text-blue-400"}>
                  {ev.type}
                </span>
                <span className="truncate text-slate-500">{JSON.stringify(ev.data).slice(0, 140)}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/* ---------------- Bulgular sekmesi ---------------- */

type FindingFilter = "all" | "confirmed" | "candidate" | "lead" | "rejected";

const FINDING_FILTERS: { value: FindingFilter; label: string; hint: string }[] = [
  { value: "all", label: "Tümü", hint: "Reddedilenler gizliyken kalan tüm bulgular" },
  { value: "confirmed", label: "Onaylanan", hint: "Güven skoru ≥ 0.80" },
  { value: "candidate", label: "Aday", hint: "Güven skoru 0.50 – 0.79" },
  { value: "lead", label: "İz", hint: "İpucu (lead): isim/okul benzerliği olan, doğrulanmamış sonuç" },
  { value: "rejected", label: "Reddedilen", hint: "Güven skoru < 0.50 ve anlamlı sinyal yok" },
];

/** Skor kırılımı sinyalleri — packages/engine/showis_engine/resolver.py ağırlıkları. */
const SIGNAL_META: Record<string, { label: string; hint: string }> = {
  username_exact_match: {
    label: "Kullanıcı adı eşleşmesi",
    hint: "Aynı kullanıcı adı en az 2 platformda (0.30)",
  },
  name_similarity: {
    label: "İsim benzerliği",
    hint: "Hedef ad ile profil adı arasındaki benzerlik (0.20)",
  },
  cross_link: { label: "Çapraz bağlantı", hint: "Profiller arası doğrulanmış bağlantı (0.25)" },
  bio_location_company: {
    label: "Bio / konum / şirket / okul",
    hint: "Eşleşen her alan 0.05 puan (en fazla 0.15)",
  },
  commit_email_or_pgp: {
    label: "Commit e-postası / PGP",
    hint: "Kod katkısı üzerinden kimlik doğrulama (0.15)",
  },
  avatar_exact_hash: {
    label: "Avatar hash eşleşmesi",
    hint: "Birebir aynı profil görseli (0.10)",
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

/** Düşük güvenilirlik eşiği — backend'in uyarı cümlesiyle aynı (0.35). */
const LOW_AUTH_THRESHOLD = 0.35;

type FindingWithAuth = FindingItem & {
  authenticity?: number | null;
  authenticity_reasons?: string[];
};

const findingsLowAuthKey = (caseId: string) => `showis:findings:hide-low-auth:${caseId}`;

function readStoredHideLowAuth(caseId: string): boolean {
  if (typeof window === "undefined") return false;
  return window.sessionStorage.getItem(findingsLowAuthKey(caseId)) === "1";
}

function isLowAuthenticity(f: FindingWithAuth): boolean {
  return typeof f.authenticity === "number" && f.authenticity < LOW_AUTH_THRESHOLD;
}

function authenticityTone(score: number): { text: string; border: string; bar: string; label: string } {
  if (score < LOW_AUTH_THRESHOLD) {
    return {
      text: "text-red-300",
      border: "border-red-500/40 bg-red-500/10",
      bar: "bg-red-500",
      label: "düşük güvenilirlik",
    };
  }
  if (score < 0.6) {
    return {
      text: "text-amber-300",
      border: "border-amber-500/40 bg-amber-500/10",
      bar: "bg-amber-400",
      label: "orta güvenilirlik",
    };
  }
  return {
    text: "text-emerald-300",
    border: "border-emerald-500/40 bg-emerald-500/10",
    bar: "bg-emerald-400",
    label: "güvenilir hesap",
  };
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
  reasons?: string[];
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  if (typeof score !== "number") return null;

  const pct = Math.round(Math.max(0, Math.min(1, score)) * 100);
  const tone = authenticityTone(score);
  const hasReasons = Boolean(reasons && reasons.length > 0);

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span
        className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium ${tone.border} ${tone.text}`}
        title={
          hasReasons
            ? `Güvenilirlik ${pct}% — ${tone.label}\n${(reasons ?? []).join("\n")}`
            : `Güvenilirlik ${pct}% — ${tone.label}`
        }
      >
        <Fingerprint size={11} aria-hidden />
        güvenilirlik <span className="mono">{pct}%</span>
      </span>
      <span className="h-1 w-14 overflow-hidden rounded-full bg-slate-800" aria-hidden>
        <span
          className={`block h-full rounded-full ${tone.bar}`}
          style={{ width: `${Math.max(4, pct)}%` }}
        />
      </span>
      {hasReasons && (
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((v) => !v)}
          className={`inline-flex items-center gap-1 rounded text-[10px] ${tone.text} underline-offset-2 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/40`}
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
          className="mt-1 w-full basis-full space-y-0.5 rounded-lg border border-slate-800 bg-slate-950/60 px-2.5 py-1.5 text-[11px] text-slate-400"
        >
          {reasons!.map((reason) => (
            <li key={reason} className="flex items-start gap-1.5">
              <span
                className={`mt-1 h-1 w-1 shrink-0 rounded-full ${tone.bar}`}
                aria-hidden
              />
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
        className="inline-flex items-center gap-1.5 rounded text-[11px] text-slate-400 transition-colors hover:text-slate-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/40"
      >
        <ChevronDown
          size={12}
          aria-hidden
          className={`transition-transform ${open ? "" : "-rotate-90"}`}
        />
        Skor kırılımı
        <span className="mono text-[10px] text-slate-500">
          {rows.length} sinyal · toplam {Math.round(total * 100)}%
        </span>
      </button>

      {open && (
        <div id={panelId} className="mt-2 rounded-lg border border-slate-800 bg-slate-950/60 p-3">
          {rows.length === 0 ? (
            <p className="text-[11px] text-slate-500">Skor sinyali kaydı yok.</p>
          ) : (
            <ul className="space-y-2">
              {rows.map((row) => {
                const negative = row.value < 0;
                return (
                  <li key={row.key} className="grid grid-cols-[1fr_auto] items-center gap-3">
                    <span className="min-w-0">
                      <span
                        className={`block truncate text-[11px] ${
                          negative ? "text-red-300" : "text-slate-300"
                        }`}
                        title={row.meta?.hint}
                      >
                        {row.meta?.label ?? row.key.replace(/_/g, " ")}
                      </span>
                      <span className="mt-1 block h-1 overflow-hidden rounded-full bg-slate-800">
                        <span
                          className={`block h-full rounded-full ${
                            negative ? "bg-red-500" : "bg-blue-400"
                          }`}
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
                      className={`mono text-[11px] ${negative ? "text-red-300" : "text-slate-200"}`}
                    >
                      {row.value > 0 ? "+" : ""}
                      {Math.round(row.value * 100)} puan
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
          <div className="mt-2 flex items-center justify-between border-t border-slate-800 pt-1.5 text-[11px]">
            <span className="text-slate-500">Toplam güven</span>
            <span className="mono font-medium text-blue-300">{Math.round(total * 100)}%</span>
          </div>
        </div>
      )}
    </div>
  );
}

function FindingCard({
  finding: f,
  evidence,
  expanded,
  highlighted = false,
  onToggleEvidence,
  onVerify,
}: {
  finding: FindingWithAuth;
  evidence?: EvidenceItem[];
  expanded: boolean;
  highlighted?: boolean;
  onToggleEvidence: (id: string) => void;
  onVerify: (id: string, status: "confirmed" | "rejected") => void;
}) {
  const pct = Math.round(f.score * 100);
  const isLead = f.status === "lead";
  const evidencePanelId = useId();

  return (
    <div
      id={`finding-${f.id}`}
      tabIndex={highlighted ? -1 : undefined}
      className={`card card-hover p-4 ${
        highlighted ? "ring-2 ring-blue-500/50 border-blue-500/40" : ""
      } focus:outline-none`}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate font-medium text-slate-100">{f.label}</span>
            <PlatformBadge platform={f.platform} />
            <Badge value={f.status} />
            <span className="mono text-sm font-medium text-blue-300" title={`Güven skoru ${pct}%`}>
              {pct}%
            </span>
            <AuthenticityChip score={f.authenticity} reasons={f.authenticity_reasons} />
          </div>
          {f.url && (
            <a
              href={f.url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-1 inline-flex items-center gap-1 break-all font-mono text-[11px] text-slate-400 transition-colors hover:text-blue-400"
            >
              {f.url}
              <ExternalLink size={10} className="shrink-0" />
            </a>
          )}
          {f.explanation && (
            <p className="mt-1.5 text-xs leading-relaxed text-slate-400">{f.explanation}</p>
          )}
          {isLead && (
            <p className="mt-1 text-[11px] text-amber-300/80">
              İpucu: isim/okul benzerliği var, tek kaynak — doğrulanmadı.
            </p>
          )}
          {f.status === "rejected" && (
            <p className="mt-1 text-[11px] text-red-300/80">
              Düşük güvenli sonuç: tek zayıf sinyal (örn. yalnızca kullanıcı adı) — varsayılan olarak
              gizlenir.
            </p>
          )}
        </div>

        <div className="flex shrink-0 flex-col items-end gap-2">
          <div className="h-1 w-24 overflow-hidden rounded-full bg-slate-800" aria-hidden>
            <div
              className={`h-full rounded-full ${
                f.status === "confirmed"
                  ? "bg-emerald-400"
                  : f.status === "rejected"
                    ? "bg-red-500"
                    : "bg-blue-400"
              }`}
              style={{ width: `${Math.max(3, pct)}%` }}
            />
          </div>
          {(f.status === "candidate" || f.status === "lead") && (
            <div className="flex gap-1.5">
              <button onClick={() => onVerify(f.id, "confirmed")} className="btn-primary btn-sm">
                <Check size={12} /> Onayla
              </button>
              <button onClick={() => onVerify(f.id, "rejected")} className="btn-ghost btn-sm">
                <X size={12} /> Reddet
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
        className="mt-3 inline-flex items-center gap-1.5 rounded text-[11px] text-slate-400 transition-colors hover:text-slate-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/40"
      >
        <Eye size={12} />
        {expanded ? "Kanıtları gizle" : `Kanıtlar (${f.evidence_ids.length})`}
      </button>
      {expanded && (
        <div id={evidencePanelId} className="mt-2 space-y-1.5 border-l border-slate-700 pl-3">
          <p className="section-title flex items-center gap-1.5">
            <ShieldCheck size={12} /> kanıt zinciri
          </p>
          {(evidence || []).map((ev) => (
            <div key={ev.id} className="space-y-0.5 text-[11px]">
              <a
                href={ev.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 break-all text-blue-400 hover:underline"
              >
                {ev.source_url}
                <ArrowUpRight size={10} className="shrink-0" />
              </a>
              <p className="mono text-slate-500">
                {ev.kind} · sha256 {ev.content_hash.slice(0, 12)}…{" "}
                {ev.fetched_at ? new Date(ev.fetched_at).toLocaleString("tr-TR") : ""}
              </p>
            </div>
          ))}
          {evidence?.length === 0 && (
            <p className="text-[11px] text-amber-300">Kanıt kaydı yok.</p>
          )}
        </div>
      )}
    </div>
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
  // T-303 → T-318: grafikten gelen entity (bulgular sekmesinde vurgulanır).
  const [focusEntity, setFocusEntity] = useState<string | null>(null);
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
      const value = new URLSearchParams(window.location.search).get("entity");
      setFocusEntity(value && value.trim() ? value.trim() : null);
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

  const load = useCallback(() => {
    api.listFindings(caseId).then(setFindings).catch((e) => setErr(e.message));
  }, [caseId]);

  useEffect(load, [load, findingsVersion]);

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

  const verify = async (fid: string, status: "confirmed" | "rejected") => {
    await api.verifyFinding(fid, status);
    load();
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

  const shown = useMemo(
    () =>
      focusFinding && !visible.some((f) => f.id === focusFinding.id)
        ? [focusFinding, ...visible]
        : visible,
    [focusFinding, visible]
  );

  useEffect(() => {
    if (!focusFinding) return;
    const el = document.getElementById(`finding-${focusFinding.id}`);
    if (el) {
      el.scrollIntoView({ block: "center" });
      el.focus({ preventScroll: true });
    }
  }, [focusFinding]);

  if (err) return <p className="text-sm text-red-300">{err}</p>;

  const running = scan?.status === "running" || scan?.status === "queued";

  const emptyMessage =
    findings.length === 0
      ? "Bulgular için önce bir tarama çalıştırın."
      : statusFiltered.length === 0
        ? filter === "rejected"
          ? "Reddedilen bulgu yok."
          : hiddenRejected > 0 && filter === "all"
            ? `${hiddenRejected} reddedilen bulgu gizlendi — düşük güvenli sonuçlar varsayılan olarak gösterilmez.`
            : "Bu filtreye uyan bulgu yok."
        : hiddenLowAuth > 0
          ? `${hiddenLowAuth} düşük güvenilirlikli bulgu gizlendi.`
          : "Bu filtreye uyan bulgu yok.";

  return (
    <div className="space-y-4 animate-fade">
      <div className="flex flex-wrap items-center gap-2">
        <div
          role="tablist"
          aria-label="Bulgu durumuna göre filtrele"
          className="flex flex-wrap items-center gap-1.5"
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
                className={`rounded-full border px-3 py-1 text-xs transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/40 ${
                  active
                    ? "border-blue-500/50 bg-blue-500/10 text-blue-200"
                    : "border-slate-800 bg-slate-900 text-slate-400 hover:border-slate-700 hover:text-slate-200"
                }`}
              >
                {f.label}
                <span className="mono ml-1.5 text-[10px] opacity-70">{n}</span>
              </button>
            );
          })}
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <button
            type="button"
            aria-pressed={hideLowAuth}
            onClick={() => applyHideLowAuth(!hideLowAuth)}
            title={
              hideLowAuth
                ? "Düşük güvenilirlikli hesapları tekrar göster"
                : "Güvenilirliği 0.35 altındaki hesapları gizle"
            }
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/40 ${
              hideLowAuth
                ? "border-red-500/40 bg-red-500/10 text-red-200"
                : "border-slate-800 bg-slate-900 text-slate-400 hover:border-slate-700 hover:text-slate-200"
            }`}
          >
            <ShieldCheck size={12} />
            Düşük güvenilirliği gizle
            <span className="mono text-[10px] opacity-70">{lowAuthCount}</span>
          </button>
          <button
            type="button"
            aria-pressed={showRejected}
            onClick={() => applyShowRejected(!showRejected)}
            title={
              showRejected
                ? "Reddedilen bulguları tekrar gizle"
                : "Düşük güvenli reddedilen bulguları listeye ekle"
            }
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/40 ${
              showRejected
                ? "border-amber-500/40 bg-amber-500/10 text-amber-200"
                : "border-slate-800 bg-slate-900 text-slate-400 hover:border-slate-700 hover:text-slate-200"
            }`}
          >
            {showRejected ? <Eye size={12} /> : <EyeOff size={12} />}
            Reddedilenleri göster
            <span className="mono text-[10px] opacity-70">{counts.rejected}</span>
          </button>
          {running && (
            <span className="chip border-blue-500/40 bg-blue-500/10 text-blue-300">
              <span className="live-dot h-1.5 w-1.5 rounded-full bg-blue-400" />
              tarama sürüyor — bulgular canlı ekleniyor
            </span>
          )}
        </div>
      </div>

      {focusEntity && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-blue-500/40 bg-blue-500/5 px-3 py-2 text-xs text-blue-200">
          <span className="inline-flex items-center gap-1.5">
            <Eye size={13} className="text-blue-400" />
            {focusFinding ? (
              <>
                Grafikten gelen bulgu vurgulanıyor:{" "}
                <span className="font-medium">{focusFinding.label}</span>
                <span className="text-blue-300/70">({focusFinding.platform})</span>
              </>
            ) : (
              <>
                Grafikten gelen entity için bulgu bulunamadı:{" "}
                <span className="mono">{focusEntity}</span>
              </>
            )}
          </span>
          <button type="button" onClick={clearFocus} className="btn-ghost btn-sm">
            <X size={12} /> Vurguyu kaldır
          </button>
        </div>
      )}

      {hiddenLowAuth > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 text-xs text-slate-400">
          <span className="inline-flex items-center gap-1.5">
            <ShieldCheck size={13} className="text-red-400" />
            <span className="mono font-medium text-slate-200">{hiddenLowAuth}</span> düşük
            güvenilirlikli hesap gizlendi
            <span className="hidden text-slate-500 sm:inline">
              — güvenilirlik &lt; 0.35 (boş/atıl hesap sinyali)
            </span>
          </span>
          <button
            type="button"
            onClick={() => applyHideLowAuth(false)}
            className="btn-ghost btn-sm"
          >
            <Eye size={12} /> Göster
          </button>
        </div>
      )}

      {hiddenRejected > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 text-xs text-slate-400">
          <span className="inline-flex items-center gap-1.5">
            <EyeOff size={13} className="text-slate-500" />
            <span className="mono font-medium text-slate-200">{hiddenRejected}</span> reddedilen bulgu
            gizlendi
            <span className="hidden text-slate-500 sm:inline">
              — yalnızca zayıf sinyal içeren düşük güvenli sonuçlar
            </span>
          </span>
          <button
            type="button"
            onClick={() => applyShowRejected(true)}
            className="btn-ghost btn-sm"
          >
            <Eye size={12} /> Göster
          </button>
        </div>
      )}

      <div
        id="findings-panel"
        role="tabpanel"
        aria-labelledby={`findings-tab-${filter}`}
        className="space-y-4"
      >
        {shown.length === 0 ? (
          <div className="card p-10 text-center">
            <p className="text-sm text-slate-400">{emptyMessage}</p>
            {hiddenLowAuth > 0 && (
              <button
                type="button"
                onClick={() => applyHideLowAuth(false)}
                className="btn-ghost btn-sm mt-3"
              >
                <Eye size={12} /> {hiddenLowAuth} düşük güvenilirlikli bulguyu göster
              </button>
            )}
            {hiddenRejected > 0 && (
              <button
                type="button"
                onClick={() => applyShowRejected(true)}
                className="btn-ghost btn-sm mt-3"
              >
                <Eye size={12} /> {hiddenRejected} reddedilen bulguyu göster
              </button>
            )}
          </div>
        ) : (
          shown.map((f) => (
            <FindingCard
              key={f.id}
              finding={f}
              evidence={evidence[f.id]}
              expanded={expanded === f.id}
              highlighted={focusFinding?.id === f.id}
              onToggleEvidence={toggleEvidence}
              onVerify={verify}
            />
          ))
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
 * ilgili kart hiç render edilmez (sessiz geriye uyum).
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
    <div className="space-y-4">
      {summary && (
        <div className="card space-y-4 p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-100">
              <BarChart3 size={15} className="text-blue-400" aria-hidden />
              Yönetici Özeti
            </h3>
            <span className="mono text-[11px] text-slate-500">
              oluşturuldu {formatDateTime(summary.generated_at)}
            </span>
          </div>

          <dl className="grid gap-x-6 gap-y-1.5 text-xs sm:grid-cols-2">
            <div className="flex gap-2">
              <dt className="w-16 shrink-0 text-slate-500">Hedef</dt>
              <dd className="text-slate-200">{summary.target_name || "—"}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-16 shrink-0 text-slate-500">Vaka</dt>
              <dd className="truncate text-slate-200">{summary.case_name || "—"}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-16 shrink-0 text-slate-500">Mod</dt>
              <dd className="mono text-slate-200">{summary.mode || "—"}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-16 shrink-0 text-slate-500">Tarama</dt>
              <dd className="text-slate-300">
                {formatDateTime(summary.scan_started_at)} → {formatDateTime(summary.scan_finished_at)}
              </dd>
            </div>
          </dl>

          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              { label: "entity", value: totals.entities },
              { label: "platform", value: totals.platforms },
              { label: "bulgu", value: totals.findings },
              { label: "kanıt", value: totals.evidence },
            ].map((item) => (
              <div
                key={item.label}
                className="rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2"
              >
                <p className="mono text-lg font-medium text-slate-100">{item.value ?? 0}</p>
                <p className="text-[10px] uppercase tracking-wider text-slate-500">{item.label}</p>
              </div>
            ))}
          </div>

          {(statusEntries.length > 0 || otherStatuses.length > 0) && (
            <div>
              <p className="section-title mb-1.5">Durum dağılımı</p>
              <div className="flex flex-wrap items-center gap-2">
                {statusEntries.map((row) => (
                  <span key={row.status} className="inline-flex items-center gap-1.5">
                    <Badge value={row.status} />
                    <span className="mono text-[11px] text-slate-300">{row.count}</span>
                  </span>
                ))}
                {otherStatuses.map(([status, count]) => (
                  <span key={status} className="inline-flex items-center gap-1.5">
                    <Badge value={status} />
                    <span className="mono text-[11px] text-slate-300">{count}</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          {summary.confidence_note && (
            <p className="flex items-start gap-2 rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 text-xs leading-relaxed text-slate-400">
              <Activity size={13} className="mt-0.5 shrink-0 text-blue-400" aria-hidden />
              {plainNote(summary.confidence_note)}
            </p>
          )}

          {topFindings.length > 0 && (
            <div className="overflow-hidden rounded-lg border border-slate-800">
              <table className="w-full text-left text-xs">
                <caption className="section-title bg-slate-900/80 px-3 py-2 text-left">
                  En yüksek güvenli 5 bulgu
                </caption>
                <thead className="bg-slate-900/60 text-[10px] uppercase tracking-wider text-slate-500">
                  <tr>
                    <th scope="col" className="px-3 py-2 font-medium">
                      Etiket
                    </th>
                    <th scope="col" className="px-3 py-2 font-medium">
                      Platform
                    </th>
                    <th scope="col" className="px-3 py-2 font-medium">
                      Durum
                    </th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">
                      Skor
                    </th>
                    <th scope="col" className="px-3 py-2 font-medium">
                      Gerekçe
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {topFindings.slice(0, 5).map((finding, index) => (
                    <tr key={`${finding.platform}-${finding.label}-${index}`} className="border-t border-slate-800/80">
                      <th scope="row" className="max-w-[180px] truncate px-3 py-2 font-normal text-slate-200">
                        {finding.url ? (
                          <a
                            href={finding.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="hover:text-blue-300 hover:underline"
                          >
                            {finding.label}
                          </a>
                        ) : (
                          finding.label
                        )}
                      </th>
                      <td className="px-3 py-2 text-slate-400">{platformMeta(finding.platform).label}</td>
                      <td className="px-3 py-2">
                        <Badge value={finding.status} />
                      </td>
                      <td className="mono px-3 py-2 text-right text-slate-300">
                        {Math.round((finding.score ?? 0) * 100)}%
                      </td>
                      <td className="px-3 py-2 text-[11px] text-slate-500">{finding.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {platforms && (
          <div className="card overflow-hidden">
            <p className="section-title border-b border-slate-800 px-4 py-2.5">Platform dağılımı</p>
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-900/60 text-[10px] uppercase tracking-wider text-slate-500">
                <tr>
                  <th scope="col" className="px-4 py-2 font-medium">
                    Platform
                  </th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">
                    Bulgu
                  </th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">
                    En yüksek
                  </th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">
                    Ortalama
                  </th>
                </tr>
              </thead>
              <tbody>
                {platforms.slice(0, 12).map((row) => (
                  <tr key={row.platform} className="border-t border-slate-800/80">
                    <th scope="row" className="px-4 py-1.5 font-normal text-slate-200">
                      {platformMeta(row.platform).label}
                    </th>
                    <td className="mono px-4 py-1.5 text-right text-slate-300">{row.findings}</td>
                    <td className="mono px-4 py-1.5 text-right text-slate-300">
                      {Math.round((row.max_score ?? 0) * 100)}%
                    </td>
                    <td className="mono px-4 py-1.5 text-right text-slate-400">
                      {Math.round((row.avg_score ?? 0) * 100)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {platforms.length > 12 && (
              <p className="border-t border-slate-800 px-4 py-1.5 text-[10px] text-slate-500">
                +{platforms.length - 12} platform daha
              </p>
            )}
          </div>
        )}

        {(identityEdges.length > 0 || sharedGroups.length > 0 || identity?.confidence_note) && (
          <div className="card space-y-3 p-4">
            <p className="section-title flex items-center gap-1.5">
              <Users size={12} className="text-blue-400" aria-hidden /> Kimlik / çapraz bağlantı özeti
            </p>
            {identity?.confidence_note && (
              <p className="text-[11px] leading-relaxed text-slate-400">
                {plainNote(identity.confidence_note)}
              </p>
            )}

            {sharedGroups.length > 0 && (
              <div>
                <p className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">
                  Paylaşılan kullanıcı adları ({sharedGroups.length})
                </p>
                <ul className="flex flex-wrap gap-1.5">
                  {sharedGroups.slice(0, 8).map((group) => (
                    <li
                      key={group.username}
                      className="inline-flex items-center gap-1.5 rounded-full border border-slate-700 bg-slate-800/60 px-2.5 py-0.5 text-[11px] text-slate-200"
                    >
                      <span className="mono">{group.username}</span>
                      <span className="text-slate-500">·</span>
                      <span className="text-slate-400">{group.platforms.join(", ")}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {identityEdges.length > 0 && (
              <table className="w-full text-left text-xs">
                <caption className="sr-only">Çapraz bağlantılar</caption>
                <thead className="text-[10px] uppercase tracking-wider text-slate-500">
                  <tr>
                    <th scope="col" className="py-1 font-medium">
                      Tip
                    </th>
                    <th scope="col" className="py-1 text-right font-medium">
                      Güven
                    </th>
                    <th scope="col" className="py-1 font-medium">
                      Gerekçe
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {identityEdges.slice(0, 8).map((edge, index) => (
                    <tr key={`${edge.source}-${edge.target}-${index}`} className="border-t border-slate-800/80">
                      <th scope="row" className="py-1 font-normal text-slate-300">
                        {edge.type.replace(/_/g, " ")}
                      </th>
                      <td className="mono py-1 text-right text-slate-300">
                        {Math.round((edge.confidence ?? 0) * 100)}%
                      </td>
                      <td className="py-1 text-[11px] text-slate-500">{edge.reason || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>

      {timeline && (
        <div className="card overflow-hidden">
          <p className="section-title flex items-center gap-1.5 border-b border-slate-800 px-4 py-2.5">
            <CalendarClock size={12} className="text-blue-400" aria-hidden /> Kronoloji (
            {timeline.length})
          </p>
          <div className="max-h-72 overflow-y-auto">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-slate-900/95 text-[10px] uppercase tracking-wider text-slate-500">
                <tr>
                  <th scope="col" className="px-4 py-2 font-medium">
                    Tarih
                  </th>
                  <th scope="col" className="px-4 py-2 font-medium">
                    Platform
                  </th>
                  <th scope="col" className="px-4 py-2 font-medium">
                    Kayıt
                  </th>
                  <th scope="col" className="px-4 py-2 font-medium">
                    Olay
                  </th>
                </tr>
              </thead>
              <tbody>
                {timeline.slice(0, 30).map((row, index) => (
                  <tr key={`${row.date}-${row.label}-${index}`} className="border-t border-slate-800/80">
                    <th scope="row" className="mono whitespace-nowrap px-4 py-1.5 font-normal text-slate-300">
                      {formatDateTime(row.date)}
                    </th>
                    <td className="px-4 py-1.5 text-slate-400">{platformMeta(row.platform).label}</td>
                    <td className="max-w-[220px] truncate px-4 py-1.5 text-slate-200">{row.label}</td>
                    <td className="mono px-4 py-1.5 text-[11px] text-slate-500">{row.event}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {timeline.length > 30 && (
            <p className="border-t border-slate-800 px-4 py-1.5 text-[10px] text-slate-500">
              +{timeline.length - 30} kayıt daha
            </p>
          )}
        </div>
      )}

      {evidence && (
        <div className="card space-y-3 p-4">
          <p className="section-title flex items-center gap-1.5">
            <FileText size={12} className="text-blue-400" aria-hidden /> Kanıt özeti
          </p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <div className="rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2">
              <p className="mono text-lg font-medium text-slate-100">{evidence.total ?? 0}</p>
              <p className="text-[10px] uppercase tracking-wider text-slate-500">toplam kanıt</p>
            </div>
            <div className="rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2">
              <p className="mono text-lg font-medium text-slate-100">
                {evidence.linked_to_entities ?? 0}
              </p>
              <p className="text-[10px] uppercase tracking-wider text-slate-500">entity&apos;ye bağlı</p>
            </div>
            <div className="rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2">
              <p className="mono text-sm font-medium text-slate-200">
                {formatDateTime(evidence.retention_until)}
              </p>
              <p className="text-[10px] uppercase tracking-wider text-slate-500">saklama bitişi</p>
            </div>
          </div>
          {evidence.by_entity && evidence.by_entity.length > 0 && (
            <table className="w-full text-left text-xs">
              <caption className="section-title pb-1 text-left">
                En çok kanıtı olan entity&apos;ler
              </caption>
              <thead className="text-[10px] uppercase tracking-wider text-slate-500">
                <tr>
                  <th scope="col" className="py-1 font-medium">
                    Entity
                  </th>
                  <th scope="col" className="py-1 font-medium">
                    Platform
                  </th>
                  <th scope="col" className="py-1 text-right font-medium">
                    Kanıt
                  </th>
                </tr>
              </thead>
              <tbody>
                {evidence.by_entity.slice(0, 10).map((row) => (
                  <tr key={row.entity_id} className="border-t border-slate-800/80">
                    <th scope="row" className="max-w-[240px] truncate py-1 font-normal text-slate-200">
                      {row.label}
                    </th>
                    <td className="py-1 text-slate-400">{platformMeta(row.platform).label}</td>
                    <td className="mono py-1 text-right text-slate-300">{row.evidence}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
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

  const generate = async (format: "json" | "pdf") => {
    setBusy(format);
    setErr(null);
    try {
      const r = await api.createReport(caseId, format);
      setReports((prev) => [r, ...prev]);
      window.open(r.download_url, "_blank");
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
    <div className="space-y-4">
      <div className="card max-w-xl space-y-4 p-5">
        <h3 className="text-sm font-semibold text-slate-100">Rapor Oluştur</h3>
        <p className="text-sm leading-relaxed text-slate-400">
          Rapor; vaka bilgileri, bulgular, skor kırılımları ve kanıt metaverilerini içerir.
        </p>
        <div className="flex gap-2">
          <button onClick={() => generate("pdf")} disabled={busy !== null} className="btn-primary">
            {busy === "pdf" ? "Oluşturuluyor…" : "PDF Rapor"}
          </button>
          <button onClick={() => generate("json")} disabled={busy !== null} className="btn-ghost">
            {busy === "json" ? "Oluşturuluyor…" : "JSON Rapor"}
          </button>
          <button
            type="button"
            onClick={loadSummary}
            disabled={summaryBusy}
            className="btn-ghost"
            aria-label="Yönetici özetini yenile"
          >
            <RefreshCw size={13} className={summaryBusy ? "animate-spin" : ""} />
            {summaryBusy ? "Özet hazırlanıyor…" : "Özeti yenile"}
          </button>
        </div>
        {err && <p className="text-sm text-red-300">{err}</p>}
        {reports.length > 0 && (
          <div className="space-y-1">
            <p className="section-title">Son oluşturulanlar</p>
            {reports.map((r) => (
              <a
                key={r.id}
                href={r.download_url}
                className="block text-sm text-blue-400 hover:underline"
              >
                {r.format.toUpperCase()} —{" "}
                {r.created_at ? new Date(r.created_at).toLocaleString("tr-TR") : r.id}
              </a>
            ))}
          </div>
        )}
      </div>

      {!summary && summaryBusy && (
        <div className="card flex items-center gap-2 p-5 text-sm text-slate-400">
          <Loader2 size={14} className="animate-spin" aria-hidden /> Yönetici özeti hazırlanıyor…
        </div>
      )}

      {!summary && !summaryBusy && summaryErr && (
        <p className="text-sm text-amber-300">Yönetici özeti yüklenemedi: {summaryErr}</p>
      )}

      {summary && hasSections && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <p className="section-title">Yönetici özeti</p>
            {summaryBusy && (
              <span className="mono text-[10px] text-slate-500">güncelleniyor…</span>
            )}
          </div>
          <ReportSummary data={summary} />
        </div>
      )}
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

  if (err) return <p className="text-sm text-red-300">{err}</p>;

  return (
    <div className="card overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-slate-900/80 text-left">
          <tr className="text-[11px] uppercase tracking-wider text-slate-500">
            <th className="px-4 py-2.5 font-medium">Zaman</th>
            <th className="px-4 py-2.5 font-medium">Aktör</th>
            <th className="px-4 py-2.5 font-medium">Aksiyon</th>
            <th className="px-4 py-2.5 font-medium">Hedef</th>
            <th className="px-4 py-2.5 font-medium">Detay</th>
          </tr>
        </thead>
        <tbody>
          {logs.map((l) => (
            <tr key={l.id} className="border-t border-slate-800/80">
              <td className="mono whitespace-nowrap px-4 py-2 text-[11px] text-slate-400">
                {l.created_at ? new Date(l.created_at).toLocaleString("tr-TR") : ""}
              </td>
              <td className="px-4 py-2 text-slate-300">{l.actor || "—"}</td>
              <td className="px-4 py-2">
                <Badge value={l.action} />
              </td>
              <td className="px-4 py-2 text-slate-400">{l.target || "—"}</td>
              <td className="max-w-xs truncate px-4 py-2 font-mono text-[11px] text-slate-500">
                {l.details ? JSON.stringify(l.details) : ""}
              </td>
            </tr>
          ))}
          {logs.length === 0 && (
            <tr>
              <td colSpan={5} className="px-4 py-8 text-center text-slate-500">
                Kayıt yok.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
