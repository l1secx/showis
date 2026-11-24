// API istemcisi — tum istekler ayni origin uzerinden relative `/api/v1/...` yoluna gider.
// Istekler sunucu tarafindaki `app/api/[...path]/route.ts` proxy'sinden gecer; API token'i
// yalniz orada (sunucu surecinde) eklenir, tarayiciya acik bir adres/token verilmez.

export interface CaseSummary {
  id: string;
  name: string;
  purpose: string;
  legal_basis: string;
  status: string;
  owner?: string | null;
  retention_days: number;
  created_at: string;
}

/** Hedef kimliği — handle-only vakalarda `first_name`/`last_name` boş olabilir. */
export type TargetKind = "name" | "handle" | "email" | "phone" | "domain" | "ip";

/** T-358 zarfındaki liste satırı (hedef + son tarama özeti ile). */
export interface CaseListItem extends CaseSummary {
  target?: {
    kind?: TargetKind | null;
    first_name?: string | null;
    last_name?: string | null;
    nickname?: string | null;
    email?: string | null;
    phone?: string | null;
    domain?: string | null;
    ip?: string | null;
  } | null;
  seeds_count?: number;
  last_scan?: {
    mode?: string | null;
    status?: string | null;
    created_at?: string | null;
  } | null;
}

export type CaseSort =
  | "created_desc"
  | "created_asc"
  | "name_asc"
  | "updated_desc"
  | "relevance";

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
    kind?: TargetKind | null;
    first_name: string;
    last_name: string;
    nickname?: string | null;
    email?: string | null;
    phone?: string | null;
    domain?: string | null;
    ip?: string | null;
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

export interface FindingCluster {
  id: string;
  size: number;
  platforms: string[];
  max_confidence: number;
  edge_count: number;
  evidence_count: number;
  reasons?: string[];
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
  authenticity?: number | null;
  authenticity_reasons?: string[] | null;
  /** Skora katkı veren bağımsız sinyal sayısı (OSINT çapraz doğrulama). */
  corroboration?: number;
  signals?: string[];
  evidence_count?: number;
  /** Aynı kişiye bağlı hesapların kümesi (tek kayıt da döner). */
  cluster?: FindingCluster | null;
  cluster_id?: string | null;
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

export interface GraphCluster {
  id: string;
  size: number;
  platforms: string[];
  max_confidence: number;
  edge_count: number;
  evidence_count: number;
  reasons?: string[];
}

export interface GraphData {
  nodes: {
    data: {
      id: string;
      label: string;
      type?: string;
      platform?: string;
      url?: string | null;
      score?: number;
      attributes?: Record<string, any>;
      cluster_id?: string | null;
      cluster_size?: number;
      cluster_confidence?: number;
      cluster_platforms?: string[];
    };
  }[];
  edges: { data: { id: string; source: string; target: string; label?: string; type?: string; confidence?: number; reason?: string } }[];
  clusters?: GraphCluster[];
}

export interface AuditEntry {
  id: string;
  action: string;
  actor?: string | null;
  target?: string | null;
  details?: Record<string, any> | null;
  created_at?: string | null;
}

export interface AuthUser {
  username: string;
  role: string;
  active: boolean;
  created_at?: string | null;
}

export interface CaseComment {
  id: string;
  author: string;
  body: string;
  created_at?: string | null;
}

export interface CaseMonitor {
  enabled: boolean;
  interval_hours: number;
  mode: string;
  webhook_url?: string | null;
  last_scan_at?: string | null;
}

export interface CaseDiff {
  available: boolean;
  reason?: string;
  from_scan?: string | null;
  to_scan?: string | null;
  findings?: {
    new: FindingItem[];
    removed: FindingItem[];
    changed: { id: string; before: FindingItem; after: FindingItem }[];
  };
  entities?: { added: string[]; removed: string[] };
  edges?: { added: string[]; removed: string[] };
  counts?: Record<string, number>;
}

export interface AIProviderProfile {
  id: string;
  name: string;
  preset: string;
  config: Record<string, any>;
  secret_masked?: string | null;
  secret_required: boolean;
  enabled: boolean;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface PoolItem {
  id: string;
  case_id: string;
  kind: string;
  label: string;
  value: string;
  source: "user" | "scan" | "ai" | "import" | string;
  status: "proposed" | "candidate" | "confirmed" | "rejected" | string;
  confidence: number;
  attributes: Record<string, any>;
  provenance: Record<string, any>;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
}

export interface PoolList {
  items: PoolItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface ResearchCitation {
  label?: string;
  url?: string | null;
  pool_item_id?: string | null;
  node_id?: string | null;
}

export interface ResearchMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant" | "tool" | "system" | string;
  content: string;
  tool_name?: string | null;
  tool_args: Record<string, any>;
  tool_result: Record<string, any>;
  citations: ResearchCitation[];
  created_at?: string | null;
}

export interface ResearchSession {
  id: string;
  case_id: string;
  title: string;
  question?: string;
  mode: "research" | "agent" | string;
  provider_id?: string | null;
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | string;
  error?: string | null;
  activity?: string | null;
  stats?: Record<string, any>;
  final_answer?: string | null;
  citations?: ResearchCitation[];
  created_by?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  messages?: ResearchMessage[];
}

export interface ResearchMeta {
  active_provider: {
    id: string;
    name: string;
    model?: string | null;
    models?: string[];
    preset: string;
  } | null;
  limits: { research: number; agent: number };
  depth_profiles?: Record<
    string,
    {
      label: string;
      max_searches: number;
      max_pages: number;
      max_waves: number;
      wall_seconds: number;
      min_findings: number;
    }
  >;
}

export interface SupplyChainNode {
  id: string;
  case_id: string;
  kind: string;
  name: string;
  country?: string | null;
  website?: string | null;
  identifiers: Record<string, any>;
  attributes: Record<string, any>;
  source: string;
  status: string;
  confidence: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SupplyChainEdge {
  id: string;
  case_id: string;
  source_id: string;
  target_id: string;
  relation: string;
  label?: string | null;
  confidence: number;
  status: string;
  source: string;
  evidence: Record<string, any>;
  created_at?: string | null;
}

export interface SupplyChainGraph {
  nodes: SupplyChainNode[];
  edges: SupplyChainEdge[];
  kinds: string[];
  relations: string[];
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
        [row.name, row.purpose, row.target?.first_name, row.target?.last_name, row.target?.nickname]
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
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
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
  captureScreenshot: (findingId: string) =>
    req<{ finding_id: string; evidence_id: string; kind: string; content_hash: string }>(
      `/findings/${findingId}/screenshot`,
      { method: "POST" }
    ),
  getGraph: (caseId: string) => req<GraphData>(`/cases/${caseId}/graph`),
  getAudit: (caseId: string) => req<AuditEntry[]>(`/cases/${caseId}/audit`),
  getMonitor: (caseId: string) => req<CaseMonitor>(`/cases/${caseId}/monitor`),
  getComments: (caseId: string) => req<CaseComment[]>(`/cases/${caseId}/comments`),
  addComment: (caseId: string, body: string, author = "operator") =>
    req<CaseComment>(`/cases/${caseId}/comments`, {
      method: "POST",
      body: JSON.stringify({ body, author }),
    }),
  updateOwner: (caseId: string, owner: string | null) =>
    req<{ id: string; owner: string | null }>(`/cases/${caseId}/owner`, {
      method: "PATCH",
      body: JSON.stringify({ owner }),
    }),
  updateMonitor: (caseId: string, body: { enabled: boolean; interval_hours: number; mode: string }) =>
    req<CaseMonitor>(`/cases/${caseId}/monitor`, { method: "PATCH", body: JSON.stringify(body) }),
  updateWebhook: (caseId: string, url: string | null) =>
    req<{ id: string; webhook_url: string | null }>(`/cases/${caseId}/webhook`, {
      method: "PATCH",
      body: JSON.stringify({ url }),
    }),
  getDiff: (caseId: string) => req<CaseDiff>(`/cases/${caseId}/diff`),
  getMe: () => req<{ username: string; role: string; via: string }>(`/auth/me`),
  listUsers: () => req<AuthUser[]>(`/auth/users`),
  createUser: (username: string, role: string) =>
    req<{ username: string; role: string; token: string }>(`/auth/users`, {
      method: "POST",
      body: JSON.stringify({ username, role }),
    }),
  deactivateUser: (username: string) =>
    req<null>(`/auth/users/${encodeURIComponent(username)}`, { method: "DELETE" }),
  exportCase: (caseId: string) => req<any>(`/cases/${caseId}/export`),
  createReport: (caseId: string, format: "json" | "pdf" | "html") =>
    req<ReportInfo>(`/cases/${caseId}/report`, { method: "POST", body: JSON.stringify({ format }) }),
  getStix: (caseId: string) => req<any>(`/cases/${caseId}/stix`),
  getMisp: (caseId: string) => req<any>(`/cases/${caseId}/misp`),

  // --- Araştırma havuzu ---
  getPool: (caseId: string, params: { q?: string; kind?: string; source?: string; status?: string; limit?: number; offset?: number } = {}) => {
    const search = new URLSearchParams();
    if (params.q) search.set("q", params.q);
    if (params.kind) search.set("kind", params.kind);
    if (params.source) search.set("source", params.source);
    if (params.status) search.set("status", params.status);
    if (params.limit != null) search.set("limit", String(params.limit));
    if (params.offset != null) search.set("offset", String(params.offset));
    const qs = search.toString();
    return req<PoolList>(`/cases/${caseId}/pool${qs ? `?${qs}` : ""}`);
  },
  addPoolItem: (caseId: string, body: Record<string, any>) =>
    req<PoolItem>(`/cases/${caseId}/pool`, { method: "POST", body: JSON.stringify(body) }),
  updatePoolItem: (caseId: string, itemId: string, body: Record<string, any>) =>
    req<PoolItem>(`/cases/${caseId}/pool/${itemId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deletePoolItem: (caseId: string, itemId: string) =>
    req<null>(`/cases/${caseId}/pool/${itemId}`, { method: "DELETE" }),
  checkPoolBreach: (caseId: string, itemId: string) =>
    req<{ item_id: string; breach: Record<string, any> }>(
      `/cases/${caseId}/pool/${itemId}/breach-check`,
      { method: "POST" }
    ),
  breachPasswordRange: (prefix: string) =>
    req<{ prefix: string; suffixes: { suffix: string; count: number }[] }>(
      `/breach/password-range/${prefix}`
    ),

  // --- AI araştırma ---
  getResearchMeta: (caseId: string) => req<ResearchMeta>(`/cases/${caseId}/research/meta`),
  listResearch: (caseId: string) =>
    req<{ items: ResearchSession[]; total: number }>(`/cases/${caseId}/research`),
  createResearch: (
    caseId: string,
    body: {
      question: string;
      mode: string;
      depth?: "quick" | "standard" | "deep";
      provider_id?: string;
      model?: string;
    }
  ) =>
    req<ResearchSession>(`/cases/${caseId}/research`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getResearch: (caseId: string, sessionId: string) =>
    req<ResearchSession>(`/cases/${caseId}/research/${sessionId}`),
  addResearchMessage: (caseId: string, sessionId: string, question: string) =>
    req<ResearchSession>(`/cases/${caseId}/research/${sessionId}/messages`, {
      method: "POST",
      body: JSON.stringify({ question }),
    }),
  cancelResearch: (caseId: string, sessionId: string) =>
    req<{ id: string; status: string }>(`/cases/${caseId}/research/${sessionId}/cancel`, {
      method: "POST",
    }),
  deleteResearch: (caseId: string, sessionId: string) =>
    req<null>(`/cases/${caseId}/research/${sessionId}`, { method: "DELETE" }),

  // --- AI sağlayıcı profilleri ---
  listAIProviders: () => req<AIProviderProfile[]>("/ai/providers"),
  listAIProviderPresets: () =>
    req<Record<string, { base_url: string; default_model: string; description: string }>>(
      "/ai/providers/presets"
    ),
  createAIProvider: (body: Record<string, any>) =>
    req<AIProviderProfile>("/ai/providers", { method: "POST", body: JSON.stringify(body) }),
  updateAIProvider: (providerId: string, body: Record<string, any>) =>
    req<AIProviderProfile>(`/ai/providers/${providerId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  activateAIProvider: (providerId: string) =>
    req<AIProviderProfile>(`/ai/providers/${providerId}/activate`, { method: "POST" }),
  testAIProvider: (providerId: string) =>
    req<{ ok: boolean; message?: string; error?: string; model?: string }>(
      `/ai/providers/${providerId}/test`,
      { method: "POST" }
    ),
  discoverAIProviderModels: (body: {
    provider_id?: string;
    base_url?: string;
    secret?: string;
  }) =>
    req<{ models: string[] }>("/ai/providers/discover", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteAIProvider: (providerId: string) =>
    req<null>(`/ai/providers/${providerId}`, { method: "DELETE" }),

  // --- Tedarik zinciri ---
  getSupplyChain: (caseId: string) =>
    req<SupplyChainGraph>(`/cases/${caseId}/supply-chain`),
  addSupplyChainNode: (caseId: string, body: Record<string, any>) =>
    req<SupplyChainNode>(`/cases/${caseId}/supply-chain/nodes`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateSupplyChainNode: (caseId: string, nodeId: string, body: Record<string, any>) =>
    req<SupplyChainNode>(`/cases/${caseId}/supply-chain/nodes/${nodeId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteSupplyChainNode: (caseId: string, nodeId: string) =>
    req<null>(`/cases/${caseId}/supply-chain/nodes/${nodeId}`, { method: "DELETE" }),
  addSupplyChainEdge: (caseId: string, body: Record<string, any>) =>
    req<SupplyChainEdge>(`/cases/${caseId}/supply-chain/edges`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateSupplyChainEdge: (caseId: string, edgeId: string, body: Record<string, any>) =>
    req<SupplyChainEdge>(`/cases/${caseId}/supply-chain/edges/${edgeId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteSupplyChainEdge: (caseId: string, edgeId: string) =>
    req<null>(`/cases/${caseId}/supply-chain/edges/${edgeId}`, { method: "DELETE" }),
};
