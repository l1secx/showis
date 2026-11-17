"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowRight, FolderSearch, Loader2, Plus, Search, Trash2, X } from "lucide-react";
import { api, type CaseListItem, type CaseSort } from "../../lib/api";

const LEGAL_LABELS: Record<string, string> = {
  consent: "Açık rıza",
  legitimate_interest: "Meşru menfaat",
  self_check: "Öz kontrol",
};

const CASE_STATUS_LABELS: Record<string, string> = {
  active: "aktif",
  review_pending: "inceleme",
  deleted: "silinmiş",
  archived: "arşiv",
};

const SCAN_STATUS_LABELS: Record<string, string> = {
  queued: "sırada",
  running: "çalışıyor",
  completed: "tamamlandı",
  failed: "başarısız",
  cancelled: "iptal",
};

const STATUS_FILTERS = [
  { value: "", label: "Tümü" },
  { value: "active", label: "Aktif" },
  { value: "review_pending", label: "İnceleme" },
];

const SORT_OPTIONS: { value: CaseSort; label: string }[] = [
  { value: "created_desc", label: "En yeni" },
  { value: "created_asc", label: "En eski" },
  { value: "name_asc", label: "Ad (A-Z)" },
  { value: "updated_desc", label: "Son tarama" },
];

const PAGE_SIZE = 50;

export function CasesDashboard() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const statusFilter = searchParams.get("status") || "";

  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [sort, setSort] = useState<CaseSort>("created_desc");
  const [items, setItems] = useState<CaseListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [grandTotal, setGrandTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Arama kutusu: 300 ms debounce → sunucu (veya eski imajda istemci) filtresi.
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query.trim()), 300);
    return () => clearTimeout(timer);
  }, [query]);

  const refreshGrandTotal = useCallback(() => {
    api
      .listCases({ limit: 1 })
      .then((page) => setGrandTotal(page.total))
      .catch(() => setGrandTotal(null));
  }, []);

  useEffect(refreshGrandTotal, [refreshGrandTotal]);

  // Filtre/sıralama değişince ilk sayfayı yükle.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .listCases({
        q: debouncedQuery || undefined,
        status: statusFilter || undefined,
        sort,
        limit: PAGE_SIZE,
        offset: 0,
      })
      .then((page) => {
        if (cancelled) return;
        setItems(page.items);
        setTotal(page.total);
      })
      .catch((e: Error) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [debouncedQuery, statusFilter, sort]);

  const loadMore = async () => {
    setLoadingMore(true);
    setError(null);
    try {
      const page = await api.listCases({
        q: debouncedQuery || undefined,
        status: statusFilter || undefined,
        sort,
        limit: PAGE_SIZE,
        offset: items.length,
      });
      setItems((prev) => {
        const seen = new Set(prev.map((c) => c.id));
        return [...prev, ...page.items.filter((c) => !seen.has(c.id))];
      });
      setTotal(page.total);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoadingMore(false);
    }
  };

  const deleteCase = async (c: CaseListItem) => {
    if (!confirm(`"${c.name}" vakasını ve tüm verilerini silmek istediğinize emin misiniz?`)) return;
    try {
      await api.deleteCase(c.id);
      setItems((prev) => prev.filter((x) => x.id !== c.id));
      setTotal((prev) => Math.max(0, prev - 1));
      refreshGrandTotal();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const setFilter = (value: string) => {
    const params = new URLSearchParams(searchParams.toString());
    if (value) params.set("status", value);
    else params.delete("status");
    const qs = params.toString();
    router.push(qs ? `/?${qs}` : "/");
  };

  const clearFilters = () => {
    setQuery("");
    const params = new URLSearchParams(searchParams.toString());
    params.delete("status");
    const qs = params.toString();
    router.push(qs ? `/?${qs}` : "/");
  };

  const hasFilters = Boolean(query.trim() || statusFilter);
  const stats = useMemo(
    () => ({
      total: grandTotal ?? total,
      matched: total,
      shown: items.length,
    }),
    [grandTotal, total, items.length]
  );

  const countLabel = hasFilters
    ? `${total}${grandTotal != null ? ` / ${grandTotal}` : ""} vaka · filtreli`
    : `${total} vaka`;

  return (
    <div className="space-y-6 animate-in">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="section-title">operasyon paneli</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-100">Vakalar</h1>
          <p className="mt-1 max-w-xl text-sm text-slate-400">
            Kamuya açık kaynaklarda hesap tespiti, cross-platform ilişkilendirme ve kanıt zinciri.
          </p>
        </div>
        <Link href="/cases/new" className="btn-primary shrink-0">
          <Plus size={15} /> Yeni Vaka
        </Link>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {[
          { label: "Toplam vaka", value: stats.total, icon: FolderSearch },
          { label: hasFilters ? "Eşleşen" : "Listelenen", value: stats.matched, icon: ArrowRight },
          { label: "Bu sayfada", value: stats.shown, icon: Search },
        ].map((s, i) => {
          const Icon = s.icon;
          return (
            <div key={s.label} className={`card p-4 animate-in stagger-${i + 1}`}>
              <div className="flex items-center justify-between">
                <span className="text-[11px] uppercase tracking-wider text-slate-500">
                  {s.label}
                </span>
                <Icon size={14} className="text-slate-600" />
              </div>
              <p className="mono mt-2 text-2xl font-semibold text-slate-100">{s.value}</p>
            </div>
          );
        })}
      </div>

      <div className="card space-y-3 p-3">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <div className="relative flex-1">
            <Search
              size={14}
              aria-hidden
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500"
            />
            <input
              type="search"
              role="searchbox"
              aria-label="Vaka ara (ad, amaç veya hedef adı)"
              placeholder="Vaka adı, amaç veya hedef adı…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="input pl-9"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery("")}
                aria-label="Aramayı temizle"
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-slate-500 hover:text-slate-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/40"
              >
                <X size={13} />
              </button>
            )}
          </div>

          <div className="flex items-center gap-2">
            <label className="flex items-center gap-2 text-xs text-slate-400">
              <span className="whitespace-nowrap">Sırala</span>
              <select
                value={sort}
                onChange={(e) => setSort(e.target.value as CaseSort)}
                aria-label="Vaka sıralaması"
                className="rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 focus:border-blue-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/20"
              >
                {SORT_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <span className="mono text-[11px] text-slate-500" aria-live="polite">
              {loading ? "yükleniyor…" : countLabel}
            </span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div role="group" aria-label="Vaka durumu filtresi" className="flex flex-wrap gap-1.5">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f.value}
                type="button"
                aria-pressed={statusFilter === f.value}
                onClick={() => setFilter(f.value)}
                className={`rounded-full border px-3 py-1 text-xs transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/40 ${
                  statusFilter === f.value
                    ? "border-blue-500/50 bg-blue-500/10 text-blue-200"
                    : "border-slate-800 bg-slate-900 text-slate-400 hover:border-slate-700 hover:text-slate-200"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
          {hasFilters && (
            <button type="button" onClick={clearFilters} className="btn-ghost btn-sm ml-auto">
              <X size={12} /> Filtreyi temizle
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="card border-red-500/40 bg-red-500/5 p-4 text-sm text-red-300">
          API&apos;ye ulaşılamadı: {error}. API servisinin çalıştığından emin olun.
        </div>
      )}

      {loading ? (
        <p className="flex items-center gap-2 text-sm text-slate-400">
          <Loader2 size={14} className="animate-spin" aria-hidden /> Yükleniyor…
        </p>
      ) : items.length === 0 ? (
        <div className="card p-12 text-center">
          <p className="text-sm text-slate-400">
            {hasFilters ? "Aramanla eşleşen vaka yok." : "Henüz vaka yok."}
          </p>
          {hasFilters ? (
            <button type="button" onClick={clearFilters} className="btn-ghost btn-sm mt-3">
              <X size={12} /> Filtreyi temizle
            </button>
          ) : (
            <Link href="/cases/new" className="mt-3 inline-flex text-sm text-blue-400 hover:underline">
              İlk vakayı oluştur →
            </Link>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          {/* 375px'te tablo belgeyi genişletmesin: kart kırpar, iç katman kaydırır */}
          <div className="card overflow-hidden">
            <div className="w-full max-w-full overflow-x-auto">
            <table className="w-full text-sm sm:min-w-[560px]">
              <thead className="bg-slate-900/80 text-left">
                <tr className="text-[11px] uppercase tracking-wider text-slate-500">
                  <th scope="col" className="px-4 py-2.5 font-medium">
                    Vaka
                  </th>
                  <th scope="col" className="hidden px-4 py-2.5 font-medium md:table-cell">
                    Amaç
                  </th>
                  <th scope="col" className="hidden px-4 py-2.5 font-medium sm:table-cell">
                    Dayanak
                  </th>
                  <th scope="col" className="px-4 py-2.5 font-medium">
                    Durum
                  </th>
                  <th scope="col" className="hidden px-4 py-2.5 font-medium sm:table-cell">
                    Son tarama
                  </th>
                  <th scope="col" className="px-4 py-2.5 font-medium">
                    Oluşturma
                  </th>
                  <th scope="col" className="w-10 px-4 py-2.5">
                    <span className="sr-only">İşlemler</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((c) => (
                  <tr
                    key={c.id}
                    className="group border-t border-slate-800/80 transition-colors hover:bg-slate-800/30"
                  >
                    <td className="px-4 py-3">
                      <Link
                        href={`/cases/${c.id}/scan`}
                        className="font-medium text-slate-100 transition-colors hover:text-blue-300"
                      >
                        {c.name}
                      </Link>
                      <p className="mono text-[10px] text-slate-600">{c.id.slice(0, 8)}</p>
                    </td>
                    <td className="hidden max-w-xs truncate px-4 py-3 text-slate-400 md:table-cell">
                      {c.purpose}
                    </td>
                    <td className="hidden px-4 py-3 text-slate-400 sm:table-cell">
                      {LEGAL_LABELS[c.legal_basis] || c.legal_basis}
                    </td>
                    <td className="px-4 py-3">
                      <span className="chip">{CASE_STATUS_LABELS[c.status] || c.status}</span>
                    </td>
                    <td className="hidden px-4 py-3 sm:table-cell">
                      {c.last_scan?.mode || c.last_scan?.status ? (
                        <span className="flex items-center gap-1.5">
                          {c.last_scan?.mode && (
                            <span className="chip border-blue-500/30 bg-blue-500/10 text-blue-200">
                              {c.last_scan.mode}
                            </span>
                          )}
                          {c.last_scan?.status && (
                            <span className="mono text-[10px] text-slate-500">
                              {SCAN_STATUS_LABELS[c.last_scan.status] || c.last_scan.status}
                            </span>
                          )}
                        </span>
                      ) : (
                        <span className="text-slate-600">—</span>
                      )}
                    </td>
                    <td className="mono px-4 py-3 text-[11px] text-slate-400">
                      {new Date(c.created_at).toLocaleString("tr-TR", {
                        dateStyle: "short",
                        timeStyle: "short",
                      })}
                    </td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => deleteCase(c)}
                        title="Vakayı sil"
                        aria-label={`${c.name} vakasını sil`}
                        className="rounded-md p-1.5 text-slate-600 transition-colors hover:bg-red-500/10 hover:text-red-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500/40 sm:opacity-0 sm:group-hover:opacity-100"
                      >
                        <Trash2 size={14} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          </div>

          {items.length < total && (
            <div className="flex items-center justify-between gap-3">
              <p className="mono text-[11px] text-slate-500">
                {items.length}/{total} vaka gösteriliyor
              </p>
              <button
                type="button"
                onClick={loadMore}
                disabled={loadingMore}
                className="btn-ghost btn-sm"
              >
                {loadingMore ? (
                  <Loader2 size={12} className="animate-spin" aria-hidden />
                ) : (
                  <ArrowRight size={12} />
                )}
                Daha fazla yükle
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
