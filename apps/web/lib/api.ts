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
      /** "scan" tarama varlığı, "pool" araştırma havuzu kaydı. */
      origin?: string;
      status?: string | null;
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
  roles?: Record<string, string>;
  capabilities?: Record<string, { tools: boolean; stream: boolean; thinking: boolean }>;
  secret_masked?: string | null;
  secret_required: boolean;
  enabled: boolean;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProviderSetting {
  id: string;
  provider: string;
  label?: string | null;
  config: Record<string, any>;
  secret_masked?: string | null;
  enabled: boolean;
  secret_required?: boolean;
}

export interface ProviderTestResult {
  ok: boolean;
  message?: string;
  error?: string;
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

/** Toplu durum ucunun yanıtı: güncellenen ve atlanan kayıt sayısı. */
export interface PoolBulkResult {
  updated: number;
  skipped: number;
}

export interface ResearchCitation {
  label?: string;
  url?: string | null;
  pool_item_id?: string | null;
  node_id?: string | null;
  snapshot_id?: string | null;
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
  status:
    | "queued"
    | "running"
    | "awaiting_user"
    | "interrupted"
    | "completed"
    | "failed"
    | "cancelled"
    | string;
  error?: string | null;
  pending_question?: { question_id: string; question: string; options: string[] } | null;
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

export interface PivotInfo {
  id: string;
  label: string;
  kinds: string[];
  description: string;
  network: boolean;
}

/** Kimlik zinciri: onaylı bulgulardan sahip beyanlı bağlantılarla bulunan düğüm. */
export interface ChainEvidence {
  relation: string;
  weight: number;
  from: string;
  source_url: string;
  note: string;
}

export interface ChainNode {
  key: string;
  kind: "profile" | "page" | "email";
  value: string;
  network: string;
  url: string;
  depth: number;
  anchor: boolean;
  trust: number;
  evidence: ChainEvidence[];
  display_name: string;
}

export interface ChainSummary {
  anchors: number;
  discovered: number;
  high_confidence: number;
  fetches: number;
  created: number;
  updated: number;
  relations: number;
  nodes: ChainNode[];
  notes: string[];
}

export interface PivotResult {
  pivot: string;
  items: { id: string; kind: string; value: string; status: string; created: boolean }[];
  relations: { id: string; type: string }[];
  notes: string[];
}

export interface PoolRelationRow {
  id: string;
  src_item_id: string;
  dst_item_id: string;
  type: string;
  confidence: number;
  evidence: { rule: string; source: string; weight: number; note: string }[];
  status: "proposed" | "candidate" | "confirmed" | "rejected" | string;
  source: string;
}

export interface WatchAlertRow {
  id: string;
  case_id: string;
  kind: "breach" | "account" | string;
  item_id: string | null;
  title: string;
  detail: Record<string, any>;
  created_at: string | null;
  seen_at: string | null;
}

export interface EvidenceSnapshotRow {
  id: string;
  case_id: string;
  session_id: string | null;
  url: string;
  title: string;
  sha256: string;
  text_sha256: string;
  text_length: number;
  text_truncated: boolean;
  http_status: number | null;
  source: string;
  fetched_at: string | null;
  expires_at: string | null;
  excerpt?: string;
  text?: string;
  integrity_ok?: boolean;
}

export interface HypothesisRow {
  id: string;
  statement: string;
  confidence: number;
  status: "proposed" | "confirmed" | "rejected" | string;
  supporting: { rule: string; source: string; weight: number; note: string }[];
  contradicting: { rule: string; source: string; weight: number; note: string }[];
  next_checks: string[];
  rationale: string;
  item_ids: string[];
  source: string;
}

export interface ReasoningGraph {
  nodes: { id: string; kind: string; label: string; value: string; status: string }[];
  edges: PoolRelationRow[];
  clusters: { id: string; size: number; item_ids: string[]; reasons: string[] }[];
  summary: string;
}

export interface ResearchProposalItem {
  item_id: string;
  kind: string;
  label: string;
  value: string;
  status: string;
  confidence: number;
  source_url?: string | null;
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

const HTTP_MESSAGES: Record<number, string> = {
  400: "İstek geçersiz. Gönderilen bilgileri kontrol edin.",
  401:
    "Oturum doğrulanamadı. Kullanıcı anahtarınız geçersiz veya süresi dolmuş olabilir; Ayarlar > Kullanıcı oturumu bölümünden yenileyin.",
  403: "Bu işlem için yetkiniz yok.",
  404: "İstenen kayıt bulunamadı.",
  409: "Kayıt zaten mevcut veya çakışıyor.",
  413: "İstek gövdesi çok büyük.",
  422: "Gönderilen bilgiler geçersiz. Alanları kontrol edip yeniden deneyin.",
  429: "Çok fazla istek gönderildi. Kısa bir süre sonra yeniden deneyin.",
  500: "Sunucuda beklenmeyen bir hata oluştu.",
  502: "API'ye ulaşılamadı. Servisin çalıştığını kontrol edin.",
  503: "API yapılandırılmamış veya geçici olarak kullanılamıyor.",
};

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  const res = await fetch(`/api/v1${path}`, {
    ...init,
    headers,
  });
  if (!res.ok) {
    throw new Error(
      HTTP_MESSAGES[res.status] ?? `API isteği başarısız oldu (HTTP ${res.status}).`
    );
  }
  const text = await res.text();
  if (!text) return null as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new Error("Sunucudan geçersiz bir yanıt alındı.");
  }
}

/** Kullanıcı anahtarını HttpOnly oturum çerezine çevirir; başarılıysa `true`. */
export async function setUserSession(token: string): Promise<boolean> {
  const response = await fetch("/api/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  return response.ok;
}

/** HttpOnly oturum çerezini siler. */
export async function clearUserSession(): Promise<void> {
  await fetch("/api/session", { method: "DELETE" });
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
  /** Toplu durum: yalnız kullanıcı eylemi; AI yolu bu ucu kullanmaz. */
  bulkUpdatePool: (caseId: string, itemIds: string[], status: "confirmed" | "rejected") =>
    req<PoolBulkResult>(`/cases/${caseId}/pool/bulk`, {
      method: "POST",
      body: JSON.stringify({ item_ids: itemIds, status }),
    }),
  checkPoolBreach: (caseId: string, itemId: string) =>
    req<{ item_id: string; breach: Record<string, any> }>(
      `/cases/${caseId}/pool/${itemId}/breach-check`,
      { method: "POST" }
    ),
  breachPasswordRange: (prefix: string) =>
    req<{ prefix: string; suffixes: { suffix: string; count: number }[] }>(
      `/breach/password-range/${prefix}`
    ),

  // --- Muhakeme: pivot, korelasyon, hipotez ---
  listPivots: (caseId: string, itemId: string) =>
    req<{ items: PivotInfo[] }>(`/cases/${caseId}/reasoning/items/${itemId}/pivots`),
  runPivot: (caseId: string, itemId: string, pivotId: string) =>
    req<PivotResult>(`/cases/${caseId}/reasoning/items/${itemId}/pivots/${pivotId}`, {
      method: "POST",
    }),
  /** Onaylı bulgulardan kimlik zinciri; çıktı `proposed` öneri olarak havuza girer. */
  runChain: (caseId: string, body: { item_ids?: string[]; max_fetches?: number } = {}) =>
    req<ChainSummary>(`/cases/${caseId}/reasoning/chain`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  runCorrelation: (caseId: string) =>
    req<{
      relations_created: number;
      relations_updated: number;
      promoted: number;
      contradictions: { kind: string; item_ids: string[]; message: string }[];
    }>(`/cases/${caseId}/reasoning/correlate`, { method: "POST" }),
  getReasoningGraph: (caseId: string) => req<ReasoningGraph>(`/cases/${caseId}/reasoning/graph`),
  listWatchAlerts: (caseId: string, unseen = false) =>
    req<{ items: WatchAlertRow[]; total: number }>(
      `/cases/${caseId}/watch/alerts${unseen ? "?unseen=true" : ""}`
    ),
  markWatchAlertSeen: (caseId: string, alertId: string) =>
    req<WatchAlertRow>(`/cases/${caseId}/watch/alerts/${alertId}/seen`, { method: "POST" }),
  runWatch: (caseId: string) =>
    req<{ breach: number; account: number; total: number }>(`/cases/${caseId}/watch/run`, {
      method: "POST",
    }),
  listSnapshots: (caseId: string, q?: string) =>
    req<{ items: EvidenceSnapshotRow[]; total: number }>(
      `/cases/${caseId}/evidence/snapshots${q ? `?q=${encodeURIComponent(q)}` : ""}`
    ),
  getSnapshot: (caseId: string, snapshotId: string) =>
    req<EvidenceSnapshotRow>(`/cases/${caseId}/evidence/snapshots/${snapshotId}`),
  listHypotheses: (caseId: string) =>
    req<{ items: HypothesisRow[]; total: number }>(`/cases/${caseId}/reasoning/hypotheses`),
  generateHypotheses: (caseId: string) =>
    req<{ items: HypothesisRow[]; total: number }>(
      `/cases/${caseId}/reasoning/hypotheses/generate`,
      { method: "POST" }
    ),
  patchHypothesis: (caseId: string, id: string, body: Record<string, any>) =>
    req<HypothesisRow>(`/cases/${caseId}/reasoning/hypotheses/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteHypothesis: (caseId: string, id: string) =>
    req<null>(`/cases/${caseId}/reasoning/hypotheses/${id}`, { method: "DELETE" }),
  patchRelation: (caseId: string, id: string, body: Record<string, any>) =>
    req<PoolRelationRow>(`/cases/${caseId}/reasoning/relations/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  getReasoningTimeline: (caseId: string) =>
    req<{ items: { item_id: string; date: string; place: string; label: string }[] }>(
      `/cases/${caseId}/reasoning/timeline`
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
  steerResearch: (caseId: string, sessionId: string, text: string) =>
    req<{ id: string; status: string; queued?: boolean; answered?: boolean }>(
      `/cases/${caseId}/research/${sessionId}/messages`,
      { method: "POST", body: JSON.stringify({ question: text }) }
    ),
  interruptResearch: (caseId: string, sessionId: string) =>
    req<{ id: string; status: string }>(`/cases/${caseId}/research/${sessionId}/interrupt`, {
      method: "POST",
    }),
  resumeResearch: (caseId: string, sessionId: string) =>
    req<ResearchSession>(`/cases/${caseId}/research/${sessionId}/resume`, { method: "POST" }),
  answerResearch: (caseId: string, sessionId: string, questionId: string, answer: string) =>
    req<{ id: string; answered: boolean }>(`/cases/${caseId}/research/${sessionId}/answer`, {
      method: "POST",
      body: JSON.stringify({ question_id: questionId, answer }),
    }),
  listResearchProposals: (caseId: string, sessionId: string) =>
    req<{ items: ResearchProposalItem[]; total: number }>(
      `/cases/${caseId}/research/${sessionId}/proposals`
    ),
  cancelResearch: (caseId: string, sessionId: string) =>
    req<{ id: string; status: string }>(`/cases/${caseId}/research/${sessionId}/cancel`, {
      method: "POST",
    }),
  deleteResearch: (caseId: string, sessionId: string) =>
    req<null>(`/cases/${caseId}/research/${sessionId}`, { method: "DELETE" }),

  // --- Sağlayıcı ayarları (legacy) ---
  listProviderSettings: () => req<ProviderSetting[]>("/settings/providers"),
  upsertProviderSetting: (body: {
    provider: string;
    label?: string;
    config: Record<string, any>;
    secret?: string;
    enabled?: boolean;
  }) =>
    req<ProviderSetting>("/settings/providers", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  testProviderSetting: (settingId: string) =>
    req<ProviderTestResult>(`/settings/providers/${settingId}/test`, { method: "POST" }),

  // --- AI sağlayıcı profilleri ---
  listAIProviders: () => req<AIProviderProfile[]>("/ai/providers"),
  listAIProviderPresets: () =>
    req<
      Record<
        string,
        {
          base_url: string;
          default_model: string;
          description: string;
          suggested_roles?: Record<string, string>;
        }
      >
    >(
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
