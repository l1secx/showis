/**
 * Zaman çizelgesi olay modeli — DOM'suz saf mantık.
 *
 * Mevcut API yanıtları (denetim kaydı, bulgu doğrulaması, kanıt kopyası,
 * tarihli havuz kaydı) tek bir olay listesine indirgenir. Şerit (`track`)
 * kimlikleri kararlıdır; etiketler dışarıdan enjekte edilebilir, böylece
 * sözlük tek kaynaktan gelir ve bu modül testte bağımsız kalır.
 */

export type TimelineSource = "audit" | "finding" | "evidence" | "record";

/** Çizilecek tek olay. `time` epoch milisaniyedir. */
export interface TimelineEvent {
  id: string;
  time: number;
  source: TimelineSource;
  /** Şerit anahtarı (`system:scan`, `platform:github`, `evidence:research`…). */
  track: string;
  trackLabel: string;
  title: string;
  detail?: string;
  status?: string | null;
  url?: string | null;
}

export interface RawAuditEntry {
  id: string;
  action: string;
  actor?: string | null;
  target?: string | null;
  created_at?: string | null;
}

export interface RawFinding {
  id: string;
  platform: string;
  label: string;
  url?: string | null;
  status?: string | null;
  score?: number | null;
  verified_at?: string | null;
}

export interface RawEvidence {
  id: string;
  url: string;
  title?: string | null;
  source?: string | null;
  kind?: string | null;
  http_status?: number | null;
  fetched_at?: string | null;
}

export interface RawRecord {
  item_id: string;
  date: string;
  label: string;
  place?: string | null;
  status?: string | null;
}

export interface NormalizeInput {
  audit?: readonly RawAuditEntry[];
  findings?: readonly RawFinding[];
  evidence?: readonly RawEvidence[];
  records?: readonly RawRecord[];
}

export interface NormalizeOptions {
  /** Denetim aksiyonu → Türkçe etiket (varsayılan: ham ad). */
  actionLabel?: (action: string) => string;
  /** Platform anahtarı → görünen ad (varsayılan: ham anahtar). */
  platformLabel?: (platform: string) => string;
}

export interface TimelineTrack {
  track: string;
  label: string;
  count: number;
}

/**
 * Şerit türüne göre çizim tonu: `R G B` kanalı.
 *
 * Durum renkleri (yeşil/amber/kırmızı) yalnız anlam taşır; bu yüzden kaynak
 * tonları düşük doygunluklu nötr tonlardır.
 */
export function sourceTone(source: TimelineSource | string): string {
  switch (source) {
    case "finding":
      return "214 218 224";
    case "evidence":
      return "146 174 190";
    case "record":
      return "186 178 158";
    default:
      return "168 168 168";
  }
}

const collator = new Intl.Collator("tr", { sensitivity: "base" });

/** ISO tarih/`YYYY-MM-DD` → epoch ms. Geçersiz değer `null`. */
export function parseTime(value: string | null | undefined): number | null {
  if (!value) return null;
  const trimmed = String(value).trim();
  if (!trimmed) return null;
  const ms = Date.parse(trimmed);
  return Number.isFinite(ms) ? ms : null;
}

/** Denetim aksiyonunun ait olduğu şerit. */
export function auditTrack(action: string): { track: string; label: string } {
  const value = action.trim().toLowerCase();
  if (value === "connector.task" || value.startsWith("scan.") || value === "monitor.scan") {
    return { track: "system:scan", label: "Tarama" };
  }
  if (value.startsWith("research.")) return { track: "system:research", label: "AI araştırma" };
  if (value.startsWith("pool.")) return { track: "system:pool", label: "Havuz" };
  if (value.startsWith("finding.")) return { track: "system:finding", label: "Bulgu" };
  if (value.startsWith("reasoning.") || value.startsWith("hypothesis")) {
    return { track: "system:reasoning", label: "Muhakeme" };
  }
  if (value.startsWith("report.")) return { track: "system:report", label: "Rapor" };
  if (value.startsWith("watch.") || value.startsWith("monitor.")) {
    return { track: "system:watch", label: "İzleme" };
  }
  if (value.startsWith("case.")) return { track: "system:case", label: "Vaka" };
  if (value.startsWith("settings.") || value.startsWith("auth.")) {
    return { track: "system:settings", label: "Sistem" };
  }
  return { track: "system:audit", label: "Denetim" };
}

export const EVIDENCE_SOURCE_LABELS: Record<string, string> = {
  research: "Araştırma kanıtı",
  subagent: "Alt ajan kanıtı",
  pivot: "Pivot kanıtı",
  citation: "Atıf kanıtı",
};

/** Kanıt kopyasının kaynağına göre şerit. */
export function evidenceTrack(source: string | null | undefined): {
  track: string;
  label: string;
} {
  const key = (source ?? "").trim() || "other";
  const track = `evidence:${key}`;
  return { track, label: EVIDENCE_SOURCE_LABELS[key] ?? `Kanıt · ${key}` };
}

export const RECORD_TRACK = "record:event";
export const RECORD_TRACK_LABEL = "Tarihli kayıt";

function detailOf(...parts: Array<string | null | undefined>): string | undefined {
  const text = parts.filter((part): part is string => Boolean(part && part.trim())).join(" · ");
  return text || undefined;
}

function formatScore(score: number | null | undefined): string | null {
  return typeof score === "number" && Number.isFinite(score)
    ? `skor ${score.toFixed(2)}`
    : null;
}

/**
 * Tüm kaynakları tek listede birleştirir.
 *
 * Geçersiz tarihli kayıtlar sessizce düşer (zaman eksenine konamaz);
 * yinelenen kimlikler ilk görülen kaydı korur. Sonuç zamana göre artan,
 * eşitlikte kimliğe göre kararlı sıralıdır.
 */
export function normalizeEvents(
  input: NormalizeInput,
  options: NormalizeOptions = {}
): TimelineEvent[] {
  const actionLabel = options.actionLabel ?? ((action: string) => action);
  const platformLabel = options.platformLabel ?? ((platform: string) => platform);
  const byId = new Map<string, TimelineEvent>();

  const push = (event: TimelineEvent | null): void => {
    if (!event) return;
    if (!byId.has(event.id)) byId.set(event.id, event);
  };

  for (const row of input.audit ?? []) {
    const time = parseTime(row.created_at);
    if (time === null) continue;
    const lane = auditTrack(row.action);
    push({
      id: `audit:${row.id}`,
      time,
      source: "audit",
      track: lane.track,
      trackLabel: lane.label,
      title: actionLabel(row.action),
      detail: detailOf(row.actor ? `Aktör: ${row.actor}` : null, row.target ? `Hedef: ${row.target}` : null),
    });
  }

  for (const row of input.findings ?? []) {
    const time = parseTime(row.verified_at);
    if (time === null) continue;
    const platform = (row.platform ?? "").trim() || "unknown";
    push({
      id: `finding:${row.id}`,
      time,
      source: "finding",
      track: `platform:${platform}`,
      trackLabel: platformLabel(platform),
      title: row.label || platform,
      detail: detailOf("Bulgu doğrulandı", formatScore(row.score)),
      status: row.status ?? null,
      url: row.url ?? null,
    });
  }

  for (const row of input.evidence ?? []) {
    const time = parseTime(row.fetched_at);
    if (time === null) continue;
    const lane = evidenceTrack(row.source);
    push({
      id: `evidence:${row.id}`,
      time,
      source: "evidence",
      track: lane.track,
      trackLabel: lane.label,
      title: row.title?.trim() || row.url,
      detail: detailOf(row.kind, row.http_status ? `HTTP ${row.http_status}` : null),
      url: row.url,
    });
  }

  for (const row of input.records ?? []) {
    const time = parseTime(row.date);
    if (time === null) continue;
    push({
      id: `record:${row.item_id}`,
      time,
      source: "record",
      track: RECORD_TRACK,
      trackLabel: RECORD_TRACK_LABEL,
      title: row.label || row.date,
      detail: detailOf(row.place ? `Yer: ${row.place}` : null, row.date),
      status: row.status ?? null,
    });
  }

  return [...byId.values()].sort((a, b) => a.time - b.time || a.id.localeCompare(b.id));
}

/** Şeritler: olay sayısı azalan, eşitlikte Türkçe ada göre artan. */
export function groupTracks(events: readonly TimelineEvent[]): TimelineTrack[] {
  const map = new Map<string, TimelineTrack>();
  for (const event of events) {
    const found = map.get(event.track);
    if (found) {
      found.count += 1;
    } else {
      map.set(event.track, {
        track: event.track,
        label: event.trackLabel,
        count: 1,
      });
    }
  }
  return [...map.values()].sort(
    (a, b) =>
      b.count - a.count ||
      collator.compare(a.label, b.label) ||
      a.track.localeCompare(b.track)
  );
}

/** Gizlenen şeritleri dışarıda bırakır; sıra korunur. */
export function filterEvents(
  events: readonly TimelineEvent[],
  hiddenTracks: ReadonlySet<string>
): TimelineEvent[] {
  if (hiddenTracks.size === 0) return [...events];
  return events.filter((event) => !hiddenTracks.has(event.track));
}

export const SOURCE_LABELS: Record<string, string> = {
  audit: "Denetim",
  finding: "Bulgu",
  evidence: "Kanıt",
  record: "Kayıt",
};

/** Bilinmeyen kaynak gizlenmez; ham adıyla döner. */
export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}
