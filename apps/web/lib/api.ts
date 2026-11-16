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
  listCases: () => req<CaseSummary[]>("/cases"),
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
  createReport: (caseId: string, format: "json" | "pdf") =>
    req<ReportInfo>(`/cases/${caseId}/report`, { method: "POST", body: JSON.stringify({ format }) }),
};
