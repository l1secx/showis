"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  Check,
  ChevronDown,
  Loader2,
  Plus,
  Search,
  ShieldAlert,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { api, type PivotInfo, type PoolItem } from "../../../../lib/api";
import { ChainPanel } from "./chain-panel";
import { PoolBulkDialog } from "./pool-bulk-dialog";
import { buildNoiseMap } from "./pool-noise";
import { TriStateCheckbox } from "./pool-selection";

const PAGE_SIZE = 100;

const KINDS = [
  "name",
  "username",
  "email",
  "phone",
  "domain",
  "ip",
  "url",
  "person",
  "organization",
  "product",
  "location",
  "event",
  "note",
  "bio",
  "identifier",
  "other",
];

const KIND_TR: Record<string, string> = {
  name: "Ad",
  username: "Kullanıcı adı",
  email: "E-posta",
  phone: "Telefon",
  domain: "Domain",
  ip: "IP",
  url: "URL",
  person: "Kişi",
  organization: "Kurum",
  product: "Ürün",
  location: "Yer",
  event: "Olay",
  note: "Not",
  bio: "Biyografi",
  identifier: "Kimlik",
  other: "Diğer",
};

const SOURCE_TR: Record<string, string> = {
  user: "Kullanıcı",
  scan: "Tarama",
  ai: "AI",
  import: "İçe aktarma",
};

const STATUS_TR: Record<string, string> = {
  proposed: "onay bekliyor",
  candidate: "aday",
  confirmed: "onaylandı",
  rejected: "reddedildi",
};

const STATUS_TONE: Record<string, string> = {
  proposed: "text-warn",
  candidate: "text-warn",
  confirmed: "text-ok",
  rejected: "text-danger",
};

const STATUS_DOT: Record<string, string> = {
  proposed: "bg-warn",
  candidate: "bg-warn",
  confirmed: "bg-ok",
  rejected: "bg-danger",
};

function Status({ value }: { value: string }) {
  return (
    <span className={`status ${STATUS_TONE[value] || "text-ink-3"}`}>
      <span
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${STATUS_DOT[value] || "bg-ink-4"}`}
        aria-hidden
      />
      {STATUS_TR[value] || value}
    </span>
  );
}

function BreachDetails({ check }: { check: any }) {
  if (!check || typeof check !== "object") return null;
  const breached = Boolean(check.breached);
  const breaches: any[] = Array.isArray(check.breaches) ? check.breaches : [];
  const providers: string[] = Array.isArray(check.providers) ? check.providers : [];
  const providerErrors: Record<string, string> =
    check.provider_errors && typeof check.provider_errors === "object" ? check.provider_errors : {};
  const stealers: any[] = Array.isArray(check.infostealer?.stealers) ? check.infostealer.stealers : [];
  const stealerCount = Number(check.infostealer?.count || 0);
  return (
    <details className="mt-1.5 rounded-[4px] border border-line bg-sunken/60 px-2 py-1.5 text-[12px]">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 marker:content-none">
        <ShieldAlert size={11} aria-hidden className={breached ? "text-danger" : "text-ok"} />
        <span className={breached ? "font-medium text-danger" : "font-medium text-ok"}>
          {breached
            ? `${check.count || breaches.length} veri ihlali${stealerCount > 0 ? ` · ${stealerCount} infostealer kaydı` : ""}`
            : "Sızıntı: ihlal bulunamadı"}
        </span>
        <span className="truncate text-ink-3">
          {check.checked_at ? new Date(check.checked_at).toLocaleString("tr-TR") : ""}
        </span>
      </summary>
      {check.password_exposed && (
        <p className="mt-1.5 font-medium text-danger">
          Parola sızmış: evet (bu e-postayla ilişkili bir ihlalde parolalar yer alıyor).
        </p>
      )}
      {stealerCount > 0 && (
        <div className="mt-1.5 border-t border-line pt-1.5">
          <p className="font-medium text-danger">
            Infostealer: {stealerCount} kayıt (yalnız özet; parola/oturum verisi gösterilmez)
          </p>
          <ul className="mt-1 space-y-0.5 text-ink-3">
            {stealers.map((stealer, index) => (
              <li key={index}>
                {stealer.date_compromised
                  ? new Date(stealer.date_compromised).toLocaleDateString("tr-TR")
                  : "tarih yok"}
                {stealer.stealer_family ? ` · ${stealer.stealer_family}` : ""}
                {stealer.operating_system ? ` · ${stealer.operating_system}` : ""}
                {` · ${stealer.user_services || 0} kullanıcı / ${stealer.corporate_services || 0} kurumsal servis`}
              </li>
            ))}
          </ul>
        </div>
      )}
      {breached && breaches.length > 0 ? (
        <ul className="mt-1.5 space-y-2">
          {breaches.map((breach, index) => (
            <li key={`${breach.name || index}`} className="border-t border-line pt-1.5 first:border-t-0 first:pt-0">
              <p className="font-medium text-ink-2">
                {breach.title || breach.name}
                {breach.breach_date ? ` · ${breach.breach_date}` : ""}
              </p>
              {Array.isArray(breach.data_classes) && breach.data_classes.length > 0 && (
                <p className="text-ink-3">{breach.data_classes.join(", ")}</p>
              )}
              {breach.description && (
                <p className="mt-0.5 line-clamp-3 text-ink-3">{breach.description}</p>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1.5 text-ink-3">
          Bu e-posta sorgulanan sağlayıcılarda görünmüyor. Yine de yeni ihlaller oluşabilir.
        </p>
      )}
      {(providers.length > 0 || Object.keys(providerErrors).length > 0) && (
        <p className="mt-1.5 text-[12px] text-ink-3">
          Kaynaklar: {providers.join(", ") || "-"}
          {Object.keys(providerErrors).length > 0 &&
            ` · ulaşılamayan: ${Object.keys(providerErrors).join(", ")}`}
        </p>
      )}
    </details>
  );
}

const CHAIN_RELATION_TR: Record<string, string> = {
  proof: "doğrulanmış kanıt",
  owner: "sahip alanı",
  rel_me: "rel=me",
  readme: "profil README",
  aggregator: "bağlantı sayfası",
  commit: "commit e-postası",
  bio: "biyografi",
  site: "kişisel site",
  backlink: "karşılıklı bağlantı",
  name: "isim eşleşmesi",
  handle_probe: "aynı kullanıcı adı",
  search: "arama sonucu",
};

// Kimlik zinciri kaydı: hangi onaylı hesaptan, hangi kanıtla bulunduğu.
function ChainTrail({ provenance }: { provenance: PoolItem["provenance"] }) {
  const data = provenance as Record<string, unknown> | null | undefined;
  if (!data || data.method !== "chain") return null;
  const path = Array.isArray(data.chain) ? data.chain.map(String) : [];
  const evidence = Array.isArray(data.evidence)
    ? (data.evidence as { relation?: string; from?: string }[])
    : [];
  const relations = Array.from(
    new Set(evidence.map((row) => CHAIN_RELATION_TR[row.relation ?? ""] ?? row.relation ?? ""))
  ).filter(Boolean);
  return (
    <p className="mt-1 text-[12px] text-ink-3">
      Zincir: {path.length ? path.join(" → ") : "çapa"}
      {relations.length > 0 && <> · {relations.join(", ")}</>}
    </p>
  );
}

// Pivot menüsü: seçili öğeden deterministik türetme (çıktılar `proposed` olarak havuza girer).
function PivotMenu({
  caseId,
  item,
  onDone,
}: {
  caseId: string;
  item: PoolItem;
  onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [pivots, setPivots] = useState<PivotInfo[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && pivots === null) {
      try {
        setPivots((await api.listPivots(caseId, item.id)).items);
      } catch (err) {
        setMessage(err instanceof Error ? err.message : "Pivotlar alınamadı");
        setPivots([]);
      }
    }
  };

  const run = async (pivot: PivotInfo) => {
    setBusy(pivot.id);
    setMessage(null);
    try {
      const result = await api.runPivot(caseId, item.id, pivot.id);
      setMessage(
        `${result.items.length} öğe, ${result.relations.length} ilişki önerildi.` +
          (result.notes.length ? ` ${result.notes.join(" ")}` : "")
      );
      onDone();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Pivot başarısız");
    } finally {
      setBusy(null);
    }
  };

  return (
    <span className="relative inline-block">
      <button
        type="button"
        className="btn-quiet btn-sm"
        onClick={toggle}
        title="Pivot"
        aria-expanded={open}
      >
        Pivot
      </button>
      {open && (
        <span className="absolute right-0 z-10 mt-1 block w-64 rounded-panel border border-line-2 bg-overlay p-2 text-left text-[12px] shadow-popover">
          {pivots === null ? (
            <Loader2 size={12} className="animate-spin" aria-hidden />
          ) : pivots.length === 0 ? (
            <span className="text-ink-3">Bu tür için pivot yok.</span>
          ) : (
            pivots.map((pivot) => (
              <button
                key={pivot.id}
                type="button"
                disabled={busy !== null}
                onClick={() => run(pivot)}
                className="block w-full rounded-control px-2 py-1 text-left transition-colors hover:bg-hover focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/25"
                title={pivot.description}
              >
                {busy === pivot.id ? "Çalışıyor…" : pivot.label}
                {pivot.network ? " ↗" : ""}
              </button>
            ))
          )}
          {message && <span className="mt-1 block text-ink-3">{message}</span>}
        </span>
      )}
    </span>
  );
}

async function sha1Hex(text: string): Promise<string> {
  const data = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-1", data);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("")
    .toUpperCase();
}

// Pwned Passwords k-anonimlik: SHA-1 tarayıcıda hesaplanır, sunucuya yalnız ilk 5 karakter gider.
function PasswordCheck() {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    if (!password) return;
    setBusy(true);
    setResult(null);
    setError(null);
    try {
      const hash = await sha1Hex(password);
      const range = await api.breachPasswordRange(hash.slice(0, 5));
      const match = range.suffixes.find((row) => row.suffix === hash.slice(5));
      setResult(
        match
          ? `Bu parola ${match.count.toLocaleString("tr-TR")} kez sızıntılarda görülmüş. Kullanmayın.`
          : "Bu parola bilinen sızıntılarda görünmüyor."
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kontrol başarısız");
    } finally {
      setPassword("");
      setBusy(false);
    }
  };

  return (
    <details className="group border-t border-line">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-3 text-[13px] font-medium text-ink marker:content-none">
        <ShieldAlert size={14} aria-hidden />
        Parolam sızmış mı?
      </summary>
      <div className="px-5 pb-4 text-[12.5px] text-ink-3">
        <p>
          Parola tarayıcınızda SHA-1 ile özetlenir; sunucuya ve Pwned Passwords&apos;e yalnız
          özetin ilk 5 karakteri gider. Parola kaydedilmez.
        </p>
        <div className="mt-2 flex gap-2">
          <input
            type="password"
            autoComplete="off"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && run()}
            placeholder="Kontrol edilecek parola"
            className="input flex-1"
          />
          <button
            type="button"
            onClick={run}
            disabled={busy || !password}
            className="btn-secondary btn-sm"
          >
            {busy ? <Loader2 size={12} className="animate-spin" aria-hidden /> : "Kontrol et"}
          </button>
        </div>
        {result && <p className="mt-2 font-medium text-ink">{result}</p>}
        {error && <p className="mt-2 text-danger">{error}</p>}
      </div>
    </details>
  );
}

export default function PoolPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [items, setItems] = useState<PoolItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [breachBusy, setBreachBusy] = useState<string | null>(null);
  const [bulkBreach, setBulkBreach] = useState(false);

  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [kind, setKind] = useState("");
  const [source, setSource] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);

  // Gürültü önayarı: varsayılan kapalı (düşük kaliteli adaylar gizli),
  // tek anahtarla geri alınır. Yalnız istemci tarafında çalışır.
  const [showNoise, setShowNoise] = useState(false);

  // Toplu işlem yalnız kullanıcı eylemidir: seçim + açık onay diyaloğu.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkDialog, setBulkDialog] = useState<"confirmed" | "rejected" | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);

  const [newKind, setNewKind] = useState("note");
  const [newLabel, setNewLabel] = useState("");
  const [newValue, setNewValue] = useState("");
  // Olay türü: tarih/yer/kurum, tarama sorgusunu ve kanıt zincirini yönlendirir.
  const [newEventDate, setNewEventDate] = useState("");
  const [newEventPlace, setNewEventPlace] = useState("");
  const [newEventOrg, setNewEventOrg] = useState("");
  const [newEventSource, setNewEventSource] = useState("");

  const noiseReasons = useMemo(() => buildNoiseMap(items), [items]);
  const noiseCount = useMemo(
    () => items.filter((item) => noiseReasons.has(item.id)).length,
    [items, noiseReasons]
  );
  const visibleItems = useMemo(
    () => (showNoise ? items : items.filter((item) => !noiseReasons.has(item.id))),
    [items, noiseReasons, showNoise]
  );

  const selectableItems = useMemo(
    () => visibleItems.filter((item) => item.status === "proposed" || item.status === "candidate"),
    [visibleItems]
  );
  const selectedItems = useMemo(
    () => items.filter((item) => selected.has(item.id)),
    [items, selected]
  );

  const counts = useMemo(() => {
    const map: Record<string, number> = {};
    for (const item of items) map[item.status] = (map[item.status] || 0) + 1;
    return map;
  }, [items]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getPool(id, {
        q: appliedQuery || undefined,
        kind: kind || undefined,
        source: source || undefined,
        status: status || undefined,
        limit: PAGE_SIZE,
        offset,
      });
      setItems(data.items);
      setTotal(data.total);
      // Son sayfa boşaldıysa (ör. toplu işlem sonrası) son dolu sayfaya dön.
      if (data.items.length === 0 && data.total > 0 && offset > 0) {
        setOffset(Math.floor((data.total - 1) / PAGE_SIZE) * PAGE_SIZE);
      }
      setSelected((prev) => {
        const pending = new Set(
          data.items
            .filter((item) => item.status === "proposed" || item.status === "candidate")
            .map((item) => item.id)
        );
        return new Set([...prev].filter((itemId) => pending.has(itemId)));
      });
    } catch (e: any) {
      setError(e.message || "Havuz yüklenemedi");
    } finally {
      setLoading(false);
    }
  }, [id, appliedQuery, kind, source, status, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  // Filtre veya sayfa değişince seçim sıfırlanır: toplu işlem yalnız
  // ekranda görünen seçili kayıtlara uygulanır.
  useEffect(() => {
    setSelected(new Set());
  }, [appliedQuery, kind, source, status, offset]);

  const addItem = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newValue.trim()) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const attributes: Record<string, string> = {};
      if (newKind === "event") {
        if (newEventDate) attributes.date = newEventDate;
        if (newEventPlace.trim()) attributes.place = newEventPlace.trim();
        if (newEventOrg.trim()) attributes.organization = newEventOrg.trim();
        if (newEventSource.trim()) attributes.source_url = newEventSource.trim();
      }
      const created = await api.addPoolItem(id, {
        kind: newKind,
        label: newLabel.trim() || null,
        value: newValue.trim(),
        attributes,
        provenance: newEventSource.trim()
          ? { url: newEventSource.trim(), kind: "event" }
          : {},
      });
      if (newKind === "email") {
        // Anahtar yoksa endpoint 503 döner; kayıt yine de korunur.
        try {
          await api.checkPoolBreach(id, created.id);
        } catch {
          /* HIBP yapılandırılmamış olabilir */
        }
      }
      setNewLabel("");
      setNewValue("");
      setNewEventDate("");
      setNewEventPlace("");
      setNewEventOrg("");
      setNewEventSource("");
      setNotice("Havuza eklendi. Aynı değer varsa mevcut kayıtla birleştirildi.");
      if (offset !== 0) setOffset(0);
      else await load();
    } catch (err: any) {
      setError(err.message || "Kayıt eklenemedi");
    } finally {
      setSaving(false);
    }
  };

  const setItemStatus = async (item: PoolItem, next: "confirmed" | "rejected") => {
    setError(null);
    try {
      await api.updatePoolItem(id, item.id, { status: next });
      await load();
    } catch (e: any) {
      setError(e.message || "Durum güncellenemedi");
    }
  };

  const removeItem = async (item: PoolItem) => {
    if (!confirm(`"${item.label}" havuzdan silinsin mi?`)) return;
    setError(null);
    try {
      await api.deletePoolItem(id, item.id);
      await load();
    } catch (e: any) {
      setError(e.message || "Kayıt silinemedi");
    }
  };

  const toggleSelected = (itemId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
  };

  // Gürültü filtresi kapanınca gizlenen kayıtların seçimi düşer; toplu
  // işlem asla ekranda görünmeyen bir kaydı sessizce değiştirmez.
  const toggleNoise = () => {
    if (showNoise) {
      setSelected((prev) => {
        const filtered = new Set([...prev].filter((itemId) => !noiseReasons.has(itemId)));
        return filtered.size === prev.size ? prev : filtered;
      });
    }
    setShowNoise(!showNoise);
  };

  const allVisibleSelected =
    selectableItems.length > 0 && selectableItems.every((item) => selected.has(item.id));

  const toggleSelectAll = () => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allVisibleSelected) {
        for (const item of selectableItems) next.delete(item.id);
      } else {
        for (const item of selectableItems) next.add(item.id);
      }
      return next;
    });
  };

  const runBulk = async () => {
    if (!bulkDialog || selectedItems.length === 0) return;
    setBulkBusy(true);
    setBulkError(null);
    setNotice(null);
    try {
      const result = await api.bulkUpdatePool(
        id,
        selectedItems.map((item) => item.id),
        bulkDialog
      );
      setBulkDialog(null);
      setSelected(new Set());
      setNotice(
        `${result.updated} kayıt ${bulkDialog === "confirmed" ? "onaylandı" : "reddedildi"}` +
          (result.skipped > 0 ? `, ${result.skipped} kayıt atlandı.` : ".")
      );
      await load();
    } catch (e: any) {
      setBulkError(e.message || "Toplu işlem başarısız");
    } finally {
      setBulkBusy(false);
    }
  };

  const checkBreach = async (item: PoolItem, silent = false) => {
    setBreachBusy(item.id);
    setError(null);
    if (!silent) setNotice(null);
    try {
      const result = await api.checkPoolBreach(id, item.id);
      if (!silent) {
        setNotice(
          result.breach.breached
            ? `${item.value}: ${result.breach.count} ihlal bulundu.`
            : `${item.value}: HIBP'de ihlal bulunamadı.`
        );
      }
      await load();
    } catch (e: any) {
      setError(e.message || "HIBP kontrolü başarısız");
    } finally {
      setBreachBusy(null);
    }
  };

  const checkAllEmails = async () => {
    setBulkBreach(true);
    setError(null);
    try {
      const page = await api.getPool(id, { kind: "email", limit: 200 });
      const emails = page.items.filter((item) => item.kind === "email");
      if (emails.length === 0) {
        setNotice("Havuzda e-posta kaydı yok.");
        return;
      }
      for (const email of emails) {
        await api.checkPoolBreach(id, email.id);
      }
      setNotice(`${emails.length} e-posta HIBP'de kontrol edildi.`);
      await load();
    } catch (e: any) {
      setError(e.message || "Toplu HIBP kontrolü başarısız");
    } finally {
      setBulkBreach(false);
    }
  };

  const researchItem = (item: PoolItem) => {
    const snippet = item.value.slice(0, 180);
    const question = `"${snippet}" hakkında vaka havuzundaki bilgileri ve kamuya açık kaynakları kullanarak ayrıntılı araştırma yap. Bulguları kanıtlarıyla sun.`;
    router.push(`/cases/${id}/research?q=${encodeURIComponent(question)}`);
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="section-heading">Araştırma havuzu</h2>
          <p className="page-subtitle">
            Vaka girdileri, yorumlar, tarama çıktıları ve AI önerileri tek yerde. AI
            araştırması bu havuzu okur; onay bekleyen önerileri siz karara bağlarsınız.
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Link href={`/cases/${id}/scan`} className="btn-secondary btn-sm">
            Havuzla tarama
          </Link>
          <Link href={`/cases/${id}/research`} className="btn-secondary btn-sm">
            <Sparkles size={13} aria-hidden />
            AI araştırması
          </Link>
        </div>
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-[6px] border border-danger-line bg-danger-soft px-3 py-2 text-[12.5px] text-danger"
        >
          {error}
        </div>
      )}
      {notice && (
        <div
          role="status"
          className="rounded-[6px] border border-ok-line bg-ok-soft px-3 py-2 text-[12.5px] text-ok"
        >
          {notice}
        </div>
      )}

      <ChainPanel caseId={id} onDone={() => void load()} />

      <div className="panel">
        <div className="panel-head flex-wrap">
          <div>
            <h2 className="panel-title">Kayıtlar</h2>
            <p className="mt-0.5 text-[12px] text-ink-3">
              {total} kayıt · bu sayfada {counts.proposed || 0} onay bekliyor ·{" "}
              {counts.candidate || 0} aday · {counts.confirmed || 0} onaylı
            </p>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setOffset(0);
              setAppliedQuery(query.trim());
            }}
            className="flex min-w-0 flex-1 items-center gap-2 sm:flex-none"
          >
            <div className="relative min-w-0 flex-1 sm:flex-none">
              <Search
                size={13}
                aria-hidden
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-3"
              />
              <input
                className="input w-full pl-8 sm:w-[210px]"
                placeholder="Havuzda ara"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="Havuzda ara"
              />
            </div>
            <button type="submit" className="btn-secondary btn-sm">
              Ara
            </button>
          </form>
        </div>

        <div className="flex flex-wrap items-end gap-3 border-t border-line px-5 py-3">
          <label className="block">
            <span className="label">Tür</span>
            <select
              className="select w-auto"
              value={kind}
              onChange={(e) => {
                setOffset(0);
                setKind(e.target.value);
              }}
            >
              <option value="">Tümü</option>
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {KIND_TR[k]}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="label">Kaynak</span>
            <select
              className="select w-auto"
              value={source}
              onChange={(e) => {
                setOffset(0);
                setSource(e.target.value);
              }}
            >
              <option value="">Tümü</option>
              {Object.entries(SOURCE_TR).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="label">Durum</span>
            <select
              className="select w-auto"
              value={status}
              onChange={(e) => {
                setOffset(0);
                setStatus(e.target.value);
              }}
            >
              <option value="">Tümü</option>
              {Object.entries(STATUS_TR).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={checkAllEmails}
            disabled={bulkBreach || breachBusy !== null}
            className="btn-secondary btn-sm"
          >
            {bulkBreach ? (
              <Loader2 size={12} className="animate-spin" aria-hidden />
            ) : (
              <ShieldAlert size={12} aria-hidden />
            )}
            E-postaları kontrol et
          </button>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={toggleSelectAll}
              disabled={selectableItems.length === 0}
              className="btn-secondary btn-sm lg:hidden"
              title="Görünen bekleyen kayıtların tümünü seç"
            >
              {allVisibleSelected ? "Seçimi kaldır" : "Bekleyenleri seç"}
            </button>
            <button
              type="button"
              onClick={toggleNoise}
              aria-pressed={showNoise}
              className={showNoise ? "btn-secondary btn-sm" : "btn-quiet btn-sm"}
              title={
                showNoise
                  ? "Gürültü filtresini kapat: düşük kaliteli adaylar yeniden gizlenir"
                  : "Güven eşiği altı, oturum açma/404 başlıklı ve tekrar eden alanları göster"
              }
            >
              {showNoise
                ? "Gürültüyü gizle"
                : `Düşük kaliteli adayları göster (${noiseCount})`}
            </button>
          </div>
        </div>

        <PasswordCheck />

        <details className="group border-t border-line">
          <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-3 text-[13px] font-medium text-ink marker:content-none">
            <ChevronDown
              size={14}
              aria-hidden
              className="text-ink-3 transition-transform group-open:rotate-180"
            />
            <Plus size={13} aria-hidden />
            Havuza kayıt ekle
          </summary>
          <form onSubmit={addItem} className="grid gap-3 border-t border-line p-5 sm:grid-cols-4">
            <label className="block">
              <span className="label">Tür</span>
              <select
                className="select"
                value={newKind}
                onChange={(e) => setNewKind(e.target.value)}
              >
                {KINDS.map((k) => (
                  <option key={k} value={k}>
                    {KIND_TR[k]}
                  </option>
                ))}
              </select>
            </label>
            <label className="block sm:col-span-1">
              <span className="label">Etiket</span>
              <input
                className="input"
                placeholder="ör. Şirket e-postası"
                value={newLabel}
                onChange={(e) => setNewLabel(e.target.value)}
                maxLength={256}
              />
            </label>
            <label className="block sm:col-span-2">
              <span className="label">
                Değer <span className="text-danger">*</span>
              </span>
              {newKind === "note" ? (
                <textarea
                  className="input min-h-[74px] resize-y"
                  placeholder="Araştırmaya girecek bilgi"
                  value={newValue}
                  onChange={(e) => setNewValue(e.target.value)}
                  maxLength={20000}
                  required
                />
              ) : (
                <input
                  className="input"
                  placeholder="Araştırmaya girecek bilgi"
                  value={newValue}
                  onChange={(e) => setNewValue(e.target.value)}
                  maxLength={20000}
                  required
                />
              )}
            </label>
            {newKind === "event" && (
              <>
                <label className="block">
                  <span className="label">Tarih</span>
                  <input
                    type="date"
                    className="input"
                    value={newEventDate}
                    onChange={(e) => setNewEventDate(e.target.value)}
                  />
                </label>
                <label className="block">
                  <span className="label">Yer</span>
                  <input
                    className="input"
                    placeholder="Ankara"
                    value={newEventPlace}
                    onChange={(e) => setNewEventPlace(e.target.value)}
                  />
                </label>
                <label className="block">
                  <span className="label">Kurum</span>
                  <input
                    className="input"
                    placeholder="Acme"
                    value={newEventOrg}
                    onChange={(e) => setNewEventOrg(e.target.value)}
                  />
                </label>
                <label className="block">
                  <span className="label">Kaynak URL</span>
                  <input
                    className="input font-mono"
                    placeholder="https://..."
                    value={newEventSource}
                    onChange={(e) => setNewEventSource(e.target.value)}
                  />
                </label>
                <p className="text-[12px] leading-relaxed text-ink-3 sm:col-span-4">
                  Olay kaydı; tarih/yer/kurum kombinasyonuyla tarama sorgularına ve
                  AI araştırmasına bağlam verir. Kanıt bağlantısı varsa ekleyin.
                </p>
              </>
            )}
            <div className="sm:col-span-4">
              <button type="submit" disabled={saving} className="btn-primary btn-sm">
                {saving ? <Loader2 size={13} className="animate-spin" aria-hidden /> : <Plus size={13} aria-hidden />}
                Ekle
              </button>
            </div>
          </form>
        </details>

        <div className="border-t border-line">
          {loading ? (
            <p className="flex items-center gap-2 px-5 py-8 text-[13px] text-ink-3">
              <Loader2 size={14} className="animate-spin" aria-hidden />
              Havuz yükleniyor
            </p>
          ) : items.length === 0 ? (
            <div className="px-5 py-10 text-center">
              <p className="text-[13px] font-medium text-ink">
                {appliedQuery || kind || source || status
                  ? "Filtreye uyan kayıt yok"
                  : "Havuz boş"}
              </p>
              <p className="mt-1 text-[12.5px] text-ink-3">
                {appliedQuery || kind || source || status
                  ? "Arama terimini veya filtreleri gevşetip yeniden deneyin."
                  : "Vaka girdileri otomatik eklenir. Yukarıdan yeni kayıt ekleyebilirsiniz."}
              </p>
            </div>
          ) : (
            <>
              {visibleItems.length === 0 && (
                <div className="px-5 py-8 text-center">
                  <p className="text-[13px] font-medium text-ink">
                    Bu sayfada yalnız gürültü kayıtları var
                  </p>
                  <p className="mt-1 text-[12.5px] text-ink-3">
                    {noiseCount} kayıt gürültü önayarıyla gizlendi.
                  </p>
                  <button
                    type="button"
                    className="btn-secondary btn-sm mt-3"
                    onClick={() => setShowNoise(true)}
                  >
                    Düşük kaliteli adayları göster
                  </button>
                </div>
              )}

              {/* Toplu işlem çubuğu: seçim yokken görünmez. */}
              {selected.size > 0 && (
                <div
                  role="region"
                  aria-label="Toplu işlem"
                  className="flex flex-wrap items-center gap-2 border-b border-accent-line bg-accent-soft px-5 py-2.5"
                >
                  <span className="text-[12.5px] font-medium text-ink">
                    {selected.size} kayıt seçildi
                  </span>
                  <span className="text-[12px] text-ink-3">yalnız sizin kararınızla uygulanır</span>
                  <div className="ml-auto flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      className="btn-primary btn-sm"
                      onClick={() => {
                        setBulkError(null);
                        setBulkDialog("confirmed");
                      }}
                      disabled={bulkBusy}
                    >
                      <Check size={12} aria-hidden />
                      Seçilenleri onayla
                    </button>
                    <button
                      type="button"
                      className="btn-danger btn-sm"
                      onClick={() => {
                        setBulkError(null);
                        setBulkDialog("rejected");
                      }}
                      disabled={bulkBusy}
                    >
                      <X size={12} aria-hidden />
                      Seçilenleri reddet
                    </button>
                    <button
                      type="button"
                      className="btn-quiet btn-sm"
                      onClick={() => setSelected(new Set())}
                      disabled={bulkBusy}
                    >
                      Seçimi temizle
                    </button>
                  </div>
                </div>
              )}

              {/* Masaüstü tablo: sabit sütun düzeni, eylemler her zaman sağda. */}
              {visibleItems.length > 0 && (
              <div className="hidden lg:block">
                <table className="data-table w-full table-fixed">
                  <colgroup>
                    <col className="w-9" />
                    <col className="w-[20%]" />
                    <col />
                    <col className="w-[104px]" />
                    <col className="w-[320px]" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th className="px-2">
                        <TriStateCheckbox
                          checked={allVisibleSelected}
                          indeterminate={selected.size > 0}
                          disabled={selectableItems.length === 0}
                          label="Görünen bekleyen kayıtların tümünü seç"
                          onChange={toggleSelectAll}
                        />
                      </th>
                      <th>Etiket</th>
                      <th>Değer</th>
                      <th>Durum</th>
                      <th className="text-right">İşlem</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleItems.map((item) => {
                      const pending =
                        item.status === "proposed" || item.status === "candidate";
                      const reasons = noiseReasons.get(item.id);
                      return (
                        <tr key={item.id}>
                          <td className="px-2 align-top">
                            {pending ? (
                              <TriStateCheckbox
                                checked={selected.has(item.id)}
                                label={`${item.label} kaydını seç`}
                                onChange={() => toggleSelected(item.id)}
                              />
                            ) : (
                              <span className="text-ink-4" aria-hidden>
                                —
                              </span>
                            )}
                          </td>
                          <td className="align-top">
                            <p className="break-words font-medium text-ink">{item.label}</p>
                            {item.kind === "event" && (
                              <p className="mt-0.5 text-[12px] text-ink-3">
                                {[
                                  item.attributes?.date,
                                  item.attributes?.place,
                                  item.attributes?.organization,
                                ]
                                  .filter(Boolean)
                                  .join(" · ") || "Tarih/yer belirtilmedi"}
                              </p>
                            )}
                            {reasons && (
                              <p className="mt-1 text-[12px] text-ink-3">
                                Gürültü: {reasons.join(" · ")}
                              </p>
                            )}
                          </td>
                          <td className="min-w-0 align-top">
                            {item.kind === "bio" ? (
                              <details className="text-ink-2">
                                <summary className="cursor-pointer text-[12px] font-medium underline">
                                  Biyografiyi aç
                                </summary>
                                <div className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words text-[12px] leading-relaxed">
                                  {item.value}
                                </div>
                              </details>
                            ) : (
                              <span
                                className="line-clamp-2 break-words text-ink-2"
                                title={item.value}
                              >
                                {item.value}
                              </span>
                            )}
                            <p className="mt-1 text-[12px] text-ink-3">
                              {KIND_TR[item.kind] || item.kind} ·{" "}
                              {SOURCE_TR[item.source] || item.source}
                              {item.status !== "confirmed" && item.confidence > 0 && (
                                <> · güven %{Math.round(item.confidence * 100)}</>
                              )}
                            </p>
                            <ChainTrail provenance={item.provenance} />
                            {item.kind === "email" && (
                              <BreachDetails check={item.attributes?.breach_check} />
                            )}
                          </td>
                          <td className="align-top">
                            <Status value={item.status} />
                          </td>
                          <td className="align-top">
                            <div className="flex flex-wrap justify-end gap-1">
                              {pending && (
                                <>
                                  <button
                                    type="button"
                                    className="btn-quiet btn-sm"
                                    onClick={() => setItemStatus(item, "confirmed")}
                                    title="Onayla"
                                  >
                                    <Check size={12} aria-hidden />
                                    Onayla
                                  </button>
                                  <button
                                    type="button"
                                    className="btn-danger-quiet btn-sm"
                                    onClick={() => setItemStatus(item, "rejected")}
                                    title="Reddet"
                                    aria-label={`${item.label} kaydını reddet`}
                                  >
                                    <X size={12} aria-hidden />
                                  </button>
                                </>
                              )}
                              {item.kind === "email" && (
                                <button
                                  type="button"
                                  className="btn-quiet btn-sm"
                                  onClick={() => checkBreach(item)}
                                  disabled={breachBusy === item.id || bulkBreach}
                                  title="HIBP veri ihlali kontrolü"
                                  aria-label={`${item.value} için HIBP veri ihlali kontrolü`}
                                >
                                  {breachBusy === item.id ? (
                                    <Loader2 size={12} className="animate-spin" aria-hidden />
                                  ) : (
                                    <ShieldAlert size={12} aria-hidden />
                                  )}
                                </button>
                              )}
                              <PivotMenu caseId={id} item={item} onDone={load} />
                              <button
                                type="button"
                                className="btn-quiet btn-sm"
                                onClick={() => researchItem(item)}
                                title="Bu kayıtla AI araştırması başlat"
                                aria-label={`${item.value} kaydıyla AI araştırması başlat`}
                              >
                                <Sparkles size={12} aria-hidden />
                              </button>
                              <button
                                type="button"
                                className="btn-danger-quiet btn-sm"
                                onClick={() => removeItem(item)}
                                title="Sil"
                                aria-label={`${item.label} kaydını sil`}
                              >
                                <Trash2 size={12} aria-hidden />
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              )}

              {/* Mobil ve orta genişlik: tablo yerine kart düzeni. */}
              <ul className="divide-y divide-line lg:hidden">
                {visibleItems.map((item) => {
                  const pending = item.status === "proposed" || item.status === "candidate";
                  const reasons = noiseReasons.get(item.id);
                  return (
                    <li key={item.id} className="px-4 py-4">
                      <div className="flex items-start gap-3">
                        <div className="pt-0.5">
                          {pending ? (
                            <TriStateCheckbox
                              checked={selected.has(item.id)}
                              label={`${item.label} kaydını seç`}
                              onChange={() => toggleSelected(item.id)}
                            />
                          ) : (
                            <span className="text-ink-4" aria-hidden>
                              —
                            </span>
                          )}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-start justify-between gap-2">
                            <p className="break-words text-[13px] font-medium text-ink">
                              {item.label}
                            </p>
                            <Status value={item.status} />
                          </div>
                          <p className="mt-0.5 line-clamp-3 break-words text-[12.5px] text-ink-2">
                            {item.value}
                          </p>
                          <p className="mt-1 text-[12px] text-ink-3">
                            {KIND_TR[item.kind] || item.kind} ·{" "}
                            {SOURCE_TR[item.source] || item.source}
                          </p>
                          {item.kind === "event" && (
                            <p className="mt-0.5 text-[12px] text-ink-3">
                              {[
                                item.attributes?.date,
                                item.attributes?.place,
                                item.attributes?.organization,
                              ]
                                .filter(Boolean)
                                .join(" · ") || "Tarih/yer belirtilmedi"}
                            </p>
                          )}
                          {reasons && (
                            <p className="mt-1 text-[12px] text-ink-3">
                              Gürültü: {reasons.join(" · ")}
                            </p>
                          )}
                          {item.kind === "email" && (
                            <BreachDetails check={item.attributes?.breach_check} />
                          )}
                          <div className="mt-2 flex flex-wrap gap-1">
                            {pending && (
                              <>
                                <button
                                  type="button"
                                  className="btn-quiet btn-sm"
                                  onClick={() => setItemStatus(item, "confirmed")}
                                >
                                  Onayla
                                </button>
                                <button
                                  type="button"
                                  className="btn-danger-quiet btn-sm"
                                  onClick={() => setItemStatus(item, "rejected")}
                                >
                                  Reddet
                                </button>
                              </>
                            )}
                            {item.kind === "email" && (
                              <button
                                type="button"
                                className="btn-quiet btn-sm"
                                onClick={() => checkBreach(item)}
                                disabled={breachBusy === item.id || bulkBreach}
                              >
                                {breachBusy === item.id ? "Kontrol ediliyor" : "Leak kontrolü"}
                              </button>
                            )}
                            <PivotMenu caseId={id} item={item} onDone={load} />
                            <button
                              type="button"
                              className="btn-quiet btn-sm"
                              onClick={() => researchItem(item)}
                            >
                              AI araştır
                            </button>
                            <button
                              type="button"
                              className="btn-danger-quiet btn-sm"
                              onClick={() => removeItem(item)}
                            >
                              Sil
                            </button>
                          </div>
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>

              {/* Sayfalama: 200+ kayıtta tablo akıcı kalır. */}
              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-5 py-3">
                <p className="text-[12px] text-ink-3">
                  {total === 0
                    ? "0 kayıt"
                    : `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} / ${total} kayıt`}
                  {!showNoise && noiseReasons.size > 0 && ` · ${noiseCount} gürültü gizli`}
                </p>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                    disabled={offset === 0 || loading}
                  >
                    Önceki
                  </button>
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    onClick={() => setOffset(offset + PAGE_SIZE)}
                    disabled={offset + PAGE_SIZE >= total || loading}
                  >
                    Sonraki
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      <PoolBulkDialog
        open={bulkDialog !== null}
        status={bulkDialog ?? "confirmed"}
        count={selectedItems.length}
        busy={bulkBusy}
        error={bulkError}
        onCancel={() => {
          if (!bulkBusy) {
            setBulkDialog(null);
            setBulkError(null);
          }
        }}
        onConfirm={() => void runBulk()}
      />
    </div>
  );
}
