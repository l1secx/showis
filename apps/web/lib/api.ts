// API istemcisi — tum istekler ayni origin uzerinden relative `/api/v1/...` yoluna gider.
// Istekler sunucu tarafindaki `app/api/[...path]/route.ts` proxy'sinden gecer; API token'i
// yalniz orada (sunucu surecinde) eklenir, tarayiciya acik bir adres/token verilmez.

export interface CaseSummary {
  id: string;
  name: string;
  purpose: string;
  legal_basis: string;
  status: string;
  retention_days: number;
  created_at: string;
}

/** T-358 zarfındaki liste satırı (hedef + son tarama özeti ile). */
export interface CaseListItem extends CaseSummary {
  target?: { first_name?: string | null; last_name?: string | null } | null;
  seeds_count?: number;
  last_scan?: {
    mode?: string | null;
    status?: string | null;
    created_at?: string | null;
  } | null;
}

export type CaseSort = "created_desc" | "created_asc" | "name_asc" | "updated_desc";

export interface CaseListParams {
  q?: string;
  status?: string;
  limit?: number;
  offset?: number;
  sort?: CaseSort;
}

export interface CaseListPage {
  items: CaseListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface CaseDetail extends CaseSummary {
  notes?: string | null;
  target?: {
    first_name: string;
    last_name: string;
    name_forms?: string[];
    hints?: Record<string, any>;
    review_required?: boolean;
  } | null;
  seeds?: { platform: string; url?: string | null; username?: string | null; email?: string | null }[];
}

export interface ScanTask {
  id: string;
  connector: string;
  platform: string;
  kind: string;
  status: string;
  attempts: number;
  observation_count: number;
  error?: string | null;
  created_at?: string | null;
  finished_at?: string | null;
}

export interface ScanStatus {
  id: string;
  case_id: string;
  status: string;
  mode: string;
  stats?: Record<string, any> | null;
  tasks_count: number;
  tasks: ScanTask[];
  started_at?: string | null;
  finished_at?: string | null;
}

export interface ScanEstimateStep {
  key: string;
  label: string;
  tasks: number;
}

export interface ScanEstimate {
  mode: string;
  seconds: number;
  tasks: number;
  requests: number;
  steps: ScanEstimateStep[];
}

export interface FindingItem {
  id: string;
  entity_id: string;
  platform: string;
  label: string;
  url?: string | null;
  status: string;
  score: number;
  breakdown?: Record<string, number> | null;
  explanation?: string | null;
  evidence_ids: string[];
  verified_at?: string | null;
}

export interface EvidenceItem {
  id: string;
  source_url: string;
  kind: string;
  content_hash: string;
  storage_key?: string | null;
  http_status?: number | null;
  user_agent?: string | null;
  fetched_at?: string | null;
  expires_at?: string | null;
  preview?: string | null;
}

export interface GraphData {
  nodes: { data: { id: string; label: string; type?: string; platform?: string; url?: string | null; score?: number; attributes?: Record<string, any> } }[];
  edges: { data: { id: string; source: string; target: string; label?: string; type?: string; confidence?: number; reason?: string } }[];
}

export interface AuditEntry {
  id: string;
  action: string;
  actor?: string | null;
  target?: string | null;
  details?: Record<string, any> | null;
  created_at?: string | null;
}

export interface ReportInfo {
  id: string;
  case_id: string;
  format: string;
  created_at?: string | null;
  download_url: string;
}

const collator = new Intl.Collator("tr", { sensitivity: "base" });

function legacySort(rows: CaseListItem[], sort?: CaseSort): CaseListItem[] {
  const sorted = [...rows];
  switch (sort) {
    case "created_asc":
      return sorted.sort((a, b) => a.created_at.localeCompare(b.created_at));
    case "name_asc":
      return sorted.sort((a, b) => collator.compare(a.name, b.name));
    case "updated_desc":
      return sorted.sort(
        (a, b) =>
          (b.last_scan?.created_at ?? b.created_at).localeCompare(
            a.last_scan?.created_at ?? a.created_at
          )
      );
    default:
      return sorted.sort((a, b) => b.created_at.localeCompare(a.created_at));
  }
}

/**
 * Vaka listesi — T-358 zarfı (`{items,total,limit,offset}`) veya eski düz dizi.
 *
 * Eski imaj düz liste döndürür ve filtre/sıralama parametrelerini yok sayar; deploy
 * sırası fark etmesin diye dizi geldiğinde arama/filtre/sıralama/sayfalama **istemcide**
 * uygulanır. Zarf geldiğinde sunucu tarafı sonuç aynen kullanılır.
 */
async function listCases(params: CaseListParams = {}): Promise<CaseListPage> {
  const search = new URLSearchParams();
  const text = (params.q ?? "").trim();
  if (text) search.set("q", text);
  if (params.status) search.set("status", params.status);
  if (params.sort) search.set("sort", params.sort);
  if (params.limit != null) search.set("limit", String(params.limit));
  if (params.offset != null) search.set("offset", String(params.offset));
  const qs = search.toString();

  const data = await req<CaseListPage | CaseListItem[]>(`/cases${qs ? `?${qs}` : ""}`);

  if (Array.isArray(data)) {
    // --- Geriye uyum: eski düz liste (T-358 öncesi imaj) ---
    let rows = data;
    if (text) {
      const needle = text.toLocaleLowerCase("tr");
      rows = rows.filter((row) =>
        [row.name, row.purpose, row.target?.first_name, row.target?.last_name]
          .filter(Boolean)
          .some((value) => String(value).toLocaleLowerCase("tr").includes(needle))
      );
    }
    if (params.status) rows = rows.filter((row) => row.status === params.status);
    rows = legacySort(rows, params.sort);
    const offset = params.offset ?? 0;
    const limit = params.limit ?? 50;
    return { items: rows.slice(offset, offset + limit), total: rows.length, limit, offset };
  }

  return {
    items: Array.isArray(data?.items) ? data.items : [],
    total: Number(data?.total ?? data?.items?.length ?? 0),
    limit: Number(data?.limit ?? params.limit ?? 50),
    offset: Number(data?.offset ?? params.offset ?? 0),
  };
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/v1${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (res.status === 401) {
    // Sunucu tarafi proxy token'i ekleyemediyse/gecersizse kullaniciya net mesaj ver.
    throw new Error("API erişimi reddedildi (token yapılandırılmamış olabilir).");
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${text || res.statusText}`);
  }
  const text = await res.text();
  return (text ? JSON.parse(text) : null) as T;
}

export const api = {
  listCases: (params: CaseListParams = {}) => listCases(params),
  getCase: (id: string) => req<CaseDetail>(`/cases/${id}`),
  createCase: (body: any) => req<{ id: string }>("/cases", { method: "POST", body: JSON.stringify(body) }),
  deleteCase: (id: string) => req<null>(`/cases/${id}`, { method: "DELETE" }),
  startScan: (caseId: string, body: any) =>
    req<{ scan_id: string }>(`/cases/${caseId}/scan`, { method: "POST", body: JSON.stringify(body) }),
  estimateScan: (caseId: string, mode: string) =>
    req<ScanEstimate>(`/cases/${caseId}/scan/estimate?mode=${encodeURIComponent(mode)}`),
  getScan: (scanId: string) => req<ScanStatus>(`/scans/${scanId}`),
  cancelScan: (scanId: string) => req<{ status: string }>(`/scans/${scanId}/cancel`, { method: "POST" }),
  listFindings: (caseId: string) => req<FindingItem[]>(`/cases/${caseId}/findings`),
  verifyFinding: (findingId: string, status: "confirmed" | "rejected") =>
    req<{ id: string; status: string }>(`/findings/${findingId}`, { method: "PATCH", body: JSON.stringify({ status }) }),
  getEvidence: (findingId: string) => req<{ finding_id: string; evidence: EvidenceItem[] }>(`/findings/${findingId}/evidence`),
  getGraph: (caseId: string) => req<GraphData>(`/cases/${caseId}/graph`),
  getAudit: (caseId: string) => req<AuditEntry[]>(`/cases/${caseId}/audit`),
  exportCase: (caseId: string) => req<any>(`/cases/${caseId}/export`),
  createReport: (caseId: string, format: "json" | "pdf" | "html") =>
    req<ReportInfo>(`/cases/${caseId}/report`, { method: "POST", body: JSON.stringify({ format }) }),
};
