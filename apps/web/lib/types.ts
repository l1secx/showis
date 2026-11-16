/* El yazımı tipler (sözleşme dışı UI/rapor tipleri).

 * Ortak sözleşme tipleri (Platform, Finding, Entity, …) `make schemas` ile
 * `./types.generated.ts` dosyasına üretilir; burada yalnızca onların üzerine
 * eklenen el yazımı tipler tutulur.
 *
 * Çakışma kuralı: bu dosyada **yerel** tanımlanan bir isim, `export *` ile
 * gelen aynı isimli tipi ezer (ES modül semantiği). Sözleşme tiplerini buraya
 * elle eklemeyin — kaynak `packages/contracts/showis_contracts` (Pydantic).
 */

export * from "./types.generated";

/* ------------------------------------------------------------------------- */
/* T-313: rapor yönetici özeti bölümleri (JSON rapor dosyasından okunur)      */
/* ------------------------------------------------------------------------- */

export interface ReportTopFinding {
  platform: string;
  label: string;
  status: string;
  score: number;
  reason: string;
  url?: string | null;
}

export interface ReportExecutiveSummary {
  target_name?: string | null;
  case_name?: string | null;
  mode?: string | null;
  scan_started_at?: string | null;
  scan_finished_at?: string | null;
  generated_at?: string | null;
  totals?: {
    entities?: number;
    platforms?: number;
    findings?: number;
    evidence?: number;
  };
  status_counts?: Record<string, number>;
  top_findings?: ReportTopFinding[];
  confidence_note?: string | null;
}

export interface ReportPlatformRow {
  platform: string;
  findings: number;
  max_score: number;
  avg_score: number;
}

export interface ReportIdentityLink {
  type: string;
  source: string;
  target: string;
  confidence: number;
  reason?: string | null;
}

export interface ReportUsernameGroup {
  username: string;
  platforms: string[];
  entities: number;
}

export interface ReportIdentityLinks {
  edges?: ReportIdentityLink[];
  shared_username_groups?: ReportUsernameGroup[];
  confidence_note?: string | null;
}

export interface ReportTimelineEntry {
  date: string;
  platform: string;
  label: string;
  event: string;
  url?: string | null;
}

export interface ReportEvidenceEntityRow {
  entity_id: string;
  platform: string;
  label: string;
  evidence: number;
}

export interface ReportEvidenceSummary {
  total: number;
  linked_to_entities: number;
  retention_until?: string | null;
  by_entity?: ReportEvidenceEntityRow[];
}

/** JSON rapor dosyasının üst yapısı — eski raporlarda bölümler yoktur. */
export interface CaseReportJson {
  generated_at?: string;
  case?: { id?: string; name?: string | null; [key: string]: unknown };
  target?: { first_name?: string | null; last_name?: string | null; [key: string]: unknown };
  findings?: unknown[];
  executive_summary?: ReportExecutiveSummary | null;
  platform_distribution?: ReportPlatformRow[] | null;
  /** Bazı sürümler bu adı kullanır (uyumluluk). */
  platform_breakdown?: ReportPlatformRow[] | null;
  identity_links?: ReportIdentityLinks | null;
  timeline?: ReportTimelineEntry[] | null;
  evidence_summary?: ReportEvidenceSummary | null;
}
